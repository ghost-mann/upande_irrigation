"""Tests for the demand calculation.

The bug being locked out: every planner was generated six days before the week it
planned and then aggregated Weather Readings from inside that future week. No
readings existed, so the deficit was zero and 819 of 836 planners instructed the
farm not to irrigate. These tests assert the window is an ELAPSED one and that an
under-covered week refuses rather than quietly returning zero.
"""

import datetime

from frappe.tests.utils import FrappeTestCase

from upande_irrigation.engine import demand as D

SETTINGS = {
	"et_crop_coefficient": 0.65,
	"default_application_rate_mm_hr": 2.8,
	"default_irrigation_coverage": 70.0,
	"min_weather_days": 7,
}


def _readings(n, rainfall=0.0, et_crop=5.0, tmin=12.0, tmax=26.0):
	return [
		{
			"rainfall_mm": rainfall,
			"et_pan": 6.0,
			"et_crop": et_crop,
			"minimum_temperature": tmin,
			"maximum_temperature": tmax,
		}
		for _ in range(n)
	]


class TestMeasuredWindow(FrappeTestCase):
	def test_window_ends_the_day_before_the_planned_week(self):
		start, end = D.measured_window(datetime.date(2026, 7, 16), days=7)
		self.assertEqual(end, datetime.date(2026, 7, 15))
		self.assertEqual(start, datetime.date(2026, 7, 9))

	def test_window_is_entirely_in_the_past_relative_to_the_plan(self):
		plan_start = datetime.date(2026, 7, 16)
		start, end = D.measured_window(plan_start)
		self.assertLess(end, plan_start)
		self.assertLess(start, end)

	def test_window_spans_exactly_the_requested_day_count(self):
		start, end = D.measured_window(datetime.date(2026, 7, 16), days=7)
		self.assertEqual((end - start).days + 1, 7)


class TestAggregate(FrappeTestCase):
	def test_counts_days_and_sums_measures(self):
		agg = D.aggregate(_readings(7, rainfall=2.0, et_crop=5.0))
		self.assertEqual(agg["days"], 7)
		self.assertAlmostEqual(agg["rainfall_mm"], 14.0)
		self.assertAlmostEqual(agg["et_crop_mm"], 35.0)

	def test_zero_temperatures_do_not_count_as_readings(self):
		agg = D.aggregate(_readings(3, tmin=0.0, tmax=0.0))
		self.assertEqual(agg["temp_days"], 0)
		self.assertEqual(agg["mean_temp"], 0.0)

	def test_empty_input_is_all_zero_not_an_error(self):
		agg = D.aggregate([])
		self.assertEqual(agg["days"], 0)
		self.assertEqual(agg["et_crop_mm"], 0.0)


class TestPlannability(FrappeTestCase):
	def test_a_full_week_is_plannable(self):
		ok, why = D.is_plannable(D.aggregate(_readings(7)), SETTINGS)
		self.assertTrue(ok)
		self.assertEqual(why, "")

	def test_an_empty_week_refuses_and_says_why(self):
		ok, why = D.is_plannable(D.aggregate([]), SETTINGS)
		self.assertFalse(ok)
		self.assertIn("0 of 7", why)

	def test_a_partial_week_refuses(self):
		ok, why = D.is_plannable(D.aggregate(_readings(4)), SETTINGS)
		self.assertFalse(ok)
		self.assertIn("4 of 7", why)


class TestDemand(FrappeTestCase):
	def test_deficit_applies_kc_then_subtracts_rain(self):
		agg = D.aggregate(_readings(7, rainfall=1.0, et_crop=5.0))
		out = D.demand(agg, carried_mm=0.0, settings=SETTINGS)
		# 35 et_crop × 0.65 = 22.75 clean; minus 7 mm rain = 15.75
		self.assertAlmostEqual(out["clean_et_crop_mm"], 22.75, places=4)
		self.assertAlmostEqual(out["week_deficit_mm"], 15.75, places=4)

	def test_rain_above_demand_yields_no_irrigation_with_a_reason(self):
		agg = D.aggregate(_readings(7, rainfall=20.0, et_crop=1.0))
		out = D.demand(agg, carried_mm=0.0, settings=SETTINGS)
		self.assertEqual(out["week_deficit_mm"], 0.0)
		self.assertEqual(out["required_hours"], 0.0)
		self.assertIn("No irrigation required", out["no_irrigation_reason"])

	def test_carried_deficit_adds_to_this_week(self):
		agg = D.aggregate(_readings(7, rainfall=1.0, et_crop=5.0))
		out = D.demand(agg, carried_mm=10.0, settings=SETTINGS)
		self.assertAlmostEqual(out["total_needed_mm"], 25.75, places=4)

	def test_carried_deficit_alone_still_asks_for_hours(self):
		agg = D.aggregate(_readings(7, rainfall=100.0, et_crop=1.0))
		out = D.demand(agg, carried_mm=10.0, settings=SETTINGS)
		self.assertEqual(out["week_deficit_mm"], 0.0)
		self.assertGreater(out["required_hours"], 0.0)
		self.assertEqual(out["no_irrigation_reason"], "")

	def test_required_hours_divide_by_rate_and_coverage(self):
		agg = D.aggregate(_readings(7, rainfall=0.0, et_crop=4.0))
		out = D.demand(agg, carried_mm=0.0, settings=SETTINGS)
		# 28 × 0.65 = 18.2 mm; 18.2 / 2.8 / 0.70 = 9.2857 h
		self.assertAlmostEqual(out["required_hours"], 9.2857, places=3)

	def test_a_zero_rate_cannot_divide_and_yields_no_hours(self):
		agg = D.aggregate(_readings(7, et_crop=4.0))
		out = D.demand(agg, 0.0, {**SETTINGS, "default_application_rate_mm_hr": 0})
		self.assertEqual(out["required_hours"], 0.0)


class TestAnthracnose(FrappeTestCase):
	def test_no_temperature_days_is_insufficient_data(self):
		z, band = D.anthracnose(D.aggregate(_readings(7, tmin=0.0, tmax=0.0)))
		self.assertEqual(z, 0.0)
		self.assertEqual(band, "Insufficient Data")

	def test_warm_wet_week_reads_high_risk(self):
		z, band = D.anthracnose(D.aggregate(_readings(7, rainfall=5.0, tmin=22.0, tmax=30.0)))
		# mean 26 °C, 35 mm rain: -58.99 + 83.72 + 6.3 = 31.03
		self.assertAlmostEqual(z, 31.03, places=2)
		self.assertEqual(band, "High Risk - Fungicide Required")
