"""Tests for allocation.

Three bugs being locked out: capacity was divided equally, so a shift with no
demand reserved as much as a parched one; every section was anchored at hour 0, so
sections sharing a pump drew on it simultaneously while the cap was computed
per-section; and cycles were scheduled back to back, which is not cycling.
"""

import datetime

from frappe.tests.utils import FrappeTestCase

from upande_irrigation.engine import allocate as A

SETTINGS = {
	"auto_cycle_threshold_hrs": 5.0,
	"cycles_when_above_threshold": 2,
	"cycles_when_below_threshold": 1,
	"cycle_rest_hours": 2.0,
}


class TestPumpCapacity(FrappeTestCase):
	def test_capacity_is_target_over_flow_rate(self):
		hours, verified = A.pump_capacity_hours(
			{"water_target_m3_per_week": 1000.0, "pump_flow_rate_m3_per_hr": 20.0}
		)
		self.assertAlmostEqual(hours, 50.0)
		self.assertTrue(verified)

	def test_no_profile_falls_back_to_a_full_week_unverified(self):
		hours, verified = A.pump_capacity_hours(None)
		self.assertAlmostEqual(hours, 168.0)
		self.assertFalse(verified)

	def test_a_profile_missing_numbers_is_also_unverified(self):
		hours, verified = A.pump_capacity_hours(
			{"water_target_m3_per_week": 0, "pump_flow_rate_m3_per_hr": 20.0}
		)
		self.assertAlmostEqual(hours, 168.0)
		self.assertFalse(verified)


class TestGrant(FrappeTestCase):
	def test_demand_within_capacity_is_granted_whole(self):
		out = A.grant([{"key": "a", "required_hours": 10.0}], capacity_hours=50.0)
		self.assertAlmostEqual(out[0]["granted_hours"], 10.0)
		self.assertFalse(out[0]["capped"])

	def test_over_capacity_scales_proportionally_not_equally(self):
		out = A.grant(
			[{"key": "a", "required_hours": 30.0}, {"key": "b", "required_hours": 10.0}],
			capacity_hours=20.0,
		)
		by = {r["key"]: r for r in out}
		# 20 / 40 = 0.5 — the thirsty shift keeps three times the share.
		self.assertAlmostEqual(by["a"]["granted_hours"], 15.0)
		self.assertAlmostEqual(by["b"]["granted_hours"], 5.0)
		self.assertTrue(by["a"]["capped"])

	def test_a_shift_needing_nothing_reserves_nothing(self):
		out = A.grant(
			[{"key": "a", "required_hours": 40.0}, {"key": "b", "required_hours": 0.0}],
			capacity_hours=20.0,
		)
		by = {r["key"]: r for r in out}
		self.assertAlmostEqual(by["b"]["granted_hours"], 0.0)
		self.assertAlmostEqual(by["a"]["granted_hours"], 20.0)
		self.assertFalse(by["b"]["capped"])

	def test_total_granted_never_exceeds_capacity(self):
		out = A.grant(
			[{"key": str(i), "required_hours": 30.0} for i in range(6)],
			capacity_hours=50.0,
		)
		self.assertLessEqual(sum(r["granted_hours"] for r in out), 50.0 + 1e-6)


class TestCyclePlan(FrappeTestCase):
	def test_short_shift_uses_the_below_threshold_count(self):
		self.assertEqual(A.cycle_plan(3.0, SETTINGS), (1, 3.0))

	def test_long_shift_splits_to_stay_under_the_threshold(self):
		count, each = A.cycle_plan(12.0, SETTINGS)
		self.assertEqual(count, 3)
		self.assertAlmostEqual(each, 4.0)
		self.assertLessEqual(each, SETTINGS["auto_cycle_threshold_hrs"])

	def test_no_hours_means_no_cycles(self):
		self.assertEqual(A.cycle_plan(0.0, SETTINGS), (0, 0.0))


class TestLayOut(FrappeTestCase):
	PLAN_FROM = datetime.date(2026, 7, 16)

	def test_cycles_are_separated_by_the_rest_interval(self):
		out = A.lay_out([{"key": "a", "granted_hours": 12.0}], self.PLAN_FROM, SETTINGS)
		windows = out[0]["windows"]
		self.assertEqual(len(windows), 3)
		gap = (windows[1][0] - windows[0][1]).total_seconds() / 3600.0
		self.assertAlmostEqual(gap, 2.0)

	def test_a_shift_needing_nothing_gets_no_window(self):
		out = A.lay_out([{"key": "a", "granted_hours": 0.0}], self.PLAN_FROM, SETTINGS)
		self.assertEqual(out[0]["windows"], [])
		self.assertEqual(out[0]["cycles_count"], 0)

	def test_shifts_on_one_pump_never_overlap(self):
		reqs = [{"key": str(i), "granted_hours": 6.0} for i in range(4)]
		out = A.lay_out(reqs, self.PLAN_FROM, SETTINGS)
		spans = sorted((w for r in out for w in r["windows"]), key=lambda w: w[0])
		self.assertGreater(len(spans), 1)
		for earlier, later in zip(spans, spans[1:]):
			self.assertLessEqual(earlier[1], later[0])

	def test_every_window_falls_inside_the_planned_week(self):
		out = A.lay_out([{"key": "a", "granted_hours": 20.0}], self.PLAN_FROM, SETTINGS)
		week_start = datetime.datetime.combine(self.PLAN_FROM, datetime.time.min)
		week_end = week_start + datetime.timedelta(days=7)
		for start, end in out[0]["windows"]:
			self.assertGreaterEqual(start, week_start)
			self.assertLessEqual(end, week_end)

	def test_demand_beyond_the_week_is_dropped_not_spilled(self):
		# 40 shifts × 6 h plus rests cannot fit in 168 h; the tail must go unplaced.
		reqs = [{"key": str(i), "granted_hours": 6.0} for i in range(40)]
		out = A.lay_out(reqs, self.PLAN_FROM, SETTINGS)
		self.assertTrue(any(r["cycles_count"] == 0 for r in out))
		week_end = datetime.datetime.combine(self.PLAN_FROM, datetime.time.min) + datetime.timedelta(days=7)
		for r in out:
			for _, end in r["windows"]:
				self.assertLessEqual(end, week_end)
