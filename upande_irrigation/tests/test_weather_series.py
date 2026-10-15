"""The weather card's endpoint (api.weather_series)."""

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
		# and to the same calendar date, for a year still under way
		self.assertIn("headline_to_date", cmp[0])

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


class TestNothingSiteSpecificIsBakedIn(IntegrationTestCase):
	"""Limits are site settings; dates in the notes come from the data."""

	def tearDown(self):
		for f in ("weather_max_plausible_temp", "long_term_min_years"):
			frappe.db.set_single_value("Irrigation Settings", f, 0)
		frappe.clear_document_cache("Irrigation Settings", "Irrigation Settings")

	def test_unset_settings_fall_back_to_the_defaults(self):
		frappe.db.set_single_value("Irrigation Settings", "weather_max_plausible_temp", 0)
		frappe.clear_document_cache("Irrigation Settings", "Irrigation Settings")
		self.assertEqual(ws.card_settings()["weather_max_plausible_temp"], ws.DEFAULTS["weather_max_plausible_temp"])

	def test_the_temperature_limit_is_the_sites_setting(self):
		farm = frappe.get_all("Farm", pluck="name", limit=1)
		if not farm:
			self.skipTest("no farm on this site")
		frappe.db.set_single_value("Irrigation Settings", "weather_max_plausible_temp", 30)
		frappe.clear_document_cache("Irrigation Settings", "Irrigation Settings")
		wr = frappe.get_doc({"doctype": "Weather Reading", "farm": farm[0], "date": "1999-07-01",
		                     "minimum_temperature": 12, "maximum_temperature": 31})
		wr.db_insert()
		try:
			vals, notes = ws.daily_values("temperature", farm[0], D(1999, 7, 1), D(1999, 7, 1))
			self.assertIsNone(vals[D(1999, 7, 1)][2])  # 31 is past this site's 30
		finally:
			frappe.db.delete("Weather Reading", {"name": wr.name})

	def test_the_long_term_years_needed_is_the_sites_setting(self):
		frappe.db.set_single_value("Irrigation Settings", "long_term_min_years", 99)
		frappe.clear_document_cache("Irrigation Settings", "Irrigation Settings")
		r = ws.series(measure="rainfall", from_date="2026-01-01", to_date="2026-03-31", granularity="month", long_term=1)
		self.assertFalse([s for s in r["series"] if s["key"] == "avg"])

	def test_no_year_or_import_history_is_written_into_the_code(self):
		import inspect, re
		src = inspect.getsource(ws)
		code = re.sub(r'"""[\\s\\S]*?"""|#[^\\n]*', "", src)  # docstrings and comments aside
		self.assertNotRegex(code, r"20[12][0-9]")
		self.assertNotIn("v15", code)


class TestExpectedRainfall(IntegrationTestCase):
	"""Bars against an expected mm/day per farm per month."""

	TODAY = D(2026, 10, 15)

	def buckets(self, targets, actual, start, end, gran, today=None):
		keys = ws.bucket_keys(start, end, gran)
		return ws.expected_buckets(targets, actual, keys, gran, start, end, today or self.TODAY)

	def test_a_week_across_a_month_takes_each_months_figure(self):
		# Mon 27 Apr – Sun 3 May 2026: four April days at 5, three May days at 7
		out = self.buckets({4: 5.0, 5: 7.0}, [41.0], D(2026, 4, 27), D(2026, 5, 3), "week")
		self.assertEqual(out[0]["mm"], 41.0)
		self.assertEqual(out[0]["status"], "met")
		self.assertFalse(out[0]["open"])

	def test_the_running_month_counts_only_the_days_so_far(self):
		out = self.buckets({10: 5.0}, [70.0], D(2026, 10, 1), D(2026, 10, 31), "month")
		self.assertEqual((out[0]["mm"], out[0]["days"], out[0]["of_days"]), (75.0, 15, 31))
		self.assertTrue(out[0]["open"])
		self.assertEqual(out[0]["status"], "below")

	def test_a_range_starting_mid_month_is_clipped(self):
		out = self.buckets({9: 2.0}, [10.0], D(2026, 9, 21), D(2026, 9, 30), "month")
		self.assertEqual((out[0]["mm"], out[0]["of_days"]), (20.0, 10))

	def test_a_month_without_a_target_leaves_its_buckets_alone(self):
		out = self.buckets({1: 1.0}, [3.0, 4.0], D(2026, 1, 1), D(2026, 2, 28), "month")
		self.assertIsNotNone(out[0])
		self.assertIsNone(out[1])

	def test_a_bucket_with_no_readings_has_no_verdict(self):
		out = self.buckets({3: 1.0}, [None], D(2026, 3, 1), D(2026, 3, 31), "month")
		self.assertEqual(out[0]["mm"], 31.0)
		self.assertIsNone(out[0]["status"])

	def test_days_after_today_have_no_target(self):
		out = self.buckets({10: 1.0, 11: 1.0}, [1.0, None], D(2026, 10, 1), D(2026, 11, 30), "month")
		self.assertIsNone(out[1])


class TestExpectedRainfallSettings(IntegrationTestCase):
	def setUp(self):
		self.farms = frappe.get_all("Farm", pluck="name", limit=2)
		if len(self.farms) < 2:
			self.skipTest("needs two farms")
		doc = frappe.get_single("Irrigation Settings")
		self.saved = (doc.rain_expected_enabled, [r.as_dict() for r in doc.rain_expected])

	def tearDown(self):
		doc = frappe.get_single("Irrigation Settings")
		doc.rain_expected_enabled = self.saved[0]
		doc.set("rain_expected", [])
		for r in self.saved[1]:
			doc.append("rain_expected", {k: r[k] for k in ("farm",) + ws.MONTHS})
		doc.save(ignore_permissions=True)
		frappe.clear_document_cache("Irrigation Settings", "Irrigation Settings")

	def configure(self, enabled, rows):
		doc = frappe.get_single("Irrigation Settings")
		doc.rain_expected_enabled = enabled
		doc.set("rain_expected", [])
		for r in rows:
			doc.append("rain_expected", r)
		doc.save(ignore_permissions=True)
		frappe.clear_document_cache("Irrigation Settings", "Irrigation Settings")

	def test_switched_off_the_card_carries_nothing(self):
		self.configure(0, [{"farm": self.farms[0], "jan": 3}])
		self.assertIsNone(ws.expected_targets(self.farms[0]))
		r = ws.series(measure="rainfall", farm=self.farms[0], from_date="2026-01-01", to_date="2026-01-31")
		self.assertIsNone(r["expected"])

	def test_all_farms_average_the_farms_that_set_a_figure(self):
		a, b = self.farms
		self.configure(1, [{"farm": a, "jan": 2, "feb": 4}, {"farm": b, "jan": 4}])
		self.assertEqual(ws.expected_targets(""), {1: 3.0, 2: 4.0})
		self.assertEqual(ws.expected_targets(b), {1: 4.0})  # 0 is "no target"

	def test_only_rainfall_is_compared(self):
		self.configure(1, [{"farm": self.farms[0], "jan": 3}])
		r = ws.series(measure="evaporation", farm=self.farms[0], from_date="2026-01-01", to_date="2026-01-31")
		self.assertIsNone(r["expected"])
		r = ws.series(measure="rainfall", farm=self.farms[0], from_date="2026-01-01", to_date="2026-01-31",
		              granularity="month")
		self.assertEqual(r["expected"][0]["mm"], 93.0)

	def test_a_farm_listed_twice_is_refused(self):
		a = self.farms[0]
		with self.assertRaises(frappe.ValidationError):
			self.configure(1, [{"farm": a, "jan": 1}, {"farm": a, "jan": 2}])

	def test_a_negative_figure_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.configure(1, [{"farm": self.farms[0], "jan": -1}])
