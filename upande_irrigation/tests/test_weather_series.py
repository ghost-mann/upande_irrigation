"""The Weather & water card's endpoint (api.weather_series)."""

import datetime

import frappe
from frappe.tests import IntegrationTestCase

from upande_irrigation.api import weather_series as ws

D = datetime.date


class TestCalendar(IntegrationTestCase):
	def test_a_comparison_day_lands_on_the_same_calendar_day(self):
		self.assertEqual(ws.shift_year(D(2025, 3, 14), 1), D(2026, 3, 14))
		# 29 Feb has nowhere to go in a common year
		self.assertEqual(ws.shift_year(D(2024, 2, 29), 1), D(2025, 2, 28))

	def test_buckets(self):
		self.assertEqual(ws.bucket_key(D(2026, 10, 8), "week"), D(2026, 10, 5))  # Monday
		self.assertEqual(ws.bucket_key(D(2026, 10, 8), "month"), D(2026, 10, 1))
		self.assertEqual(ws.bucket_key(D(2026, 10, 8), "year"), D(2026, 1, 1))
		self.assertEqual(len(ws.bucket_keys(D(2026, 1, 1), D(2026, 12, 31), "month")), 12)

	def test_coverage_never_passes_one(self):
		# a leap year's 366 days compared into 365
		vals = {D(2024, 1, 1) + datetime.timedelta(days=i): 1 for i in range(366)}
		self.assertEqual(ws.coverage(vals, D(2025, 1, 1), D(2025, 12, 31)), 1.0)


class TestAggregation(IntegrationTestCase):
	def test_totals_sum_and_a_day_with_no_reading_is_a_gap(self):
		keys = ws.bucket_keys(D(2026, 1, 1), D(2026, 1, 14), "week")
		vals = {D(2026, 1, 1): 2.0, D(2026, 1, 2): 3.0}
		out = ws.aggregate(vals, keys, "week", "rainfall")
		self.assertEqual(out[0], 5.0)
		self.assertIsNone(out[-1])  # nothing read that week: a gap, not 0

	def test_levels_average(self):
		keys = ws.bucket_keys(D(2026, 1, 5), D(2026, 1, 11), "week")
		out = ws.aggregate({D(2026, 1, 5): 10.0, D(2026, 1, 6): 20.0}, keys, "week", "deficit_swd")
		self.assertEqual(out, [15.0])

	def test_a_comparison_year_is_bucketed_on_this_years_calendar(self):
		keys = ws.bucket_keys(D(2026, 3, 1), D(2026, 3, 31), "month")
		out = ws.aggregate({D(2025, 3, 10): 7.0}, keys, "month", "rainfall", day_offset_years=1)
		self.assertEqual(out, [7.0])


class TestEndpoint(IntegrationTestCase):
	def test_every_measure_answers(self):
		for m in ws.MEASURES:
			r = ws.series(measure=m, from_date="2026-01-01", to_date="2026-01-31", granularity="week")
			self.assertEqual(r["measure"], m)
			self.assertEqual(len(r["series"][0]["values"]), len(r["buckets"]))

	def test_an_unknown_measure_falls_back_to_rainfall(self):
		self.assertEqual(ws.series(measure="nope", from_date="2026-01-01", to_date="2026-01-07")["measure"], "rainfall")

	def test_comparisons_carry_a_like_for_like_figure(self):
		r = ws.series(measure="rainfall", from_date="2026-01-01", to_date="2026-03-31",
		              granularity="month", compare_years="2025", long_term=0)
		cmp = [s for s in r["series"] if s["key"] == "2025"]
		self.assertTrue(cmp)
		self.assertIn("headline_same_days", cmp[0])

	def test_an_impossible_temperature_is_dropped_and_named(self):
		farm = frappe.get_all("Farm", pluck="name", limit=1)
		if not farm:
			self.skipTest("no farm on this site")
		day = "1999-06-15"
		wr = frappe.get_doc({"doctype": "Weather Reading", "farm": farm[0], "date": day,
		                     "minimum_temperature": 12, "maximum_temperature": 2689})
		wr.db_insert()
		try:
			vals, notes = ws.daily_values("temperature", farm[0], D(1999, 6, 15), D(1999, 6, 15))
			self.assertIsNone(vals[D(1999, 6, 15)][2])  # the max is not counted
			self.assertTrue(any("2689" in n for n in notes))
		finally:
			frappe.db.delete("Weather Reading", {"name": wr.name})
