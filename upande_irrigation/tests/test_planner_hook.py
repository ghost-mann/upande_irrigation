"""The hook must write demand and must not write allocation.

Bugs locked out: compute_shift used to set shift_hours, so opening a scheduled
planner and saving it replaced the allocator's capped figure with an uncapped one;
and carry-forward required to_date = from_date − 1 exactly, so a skipped week
silently wrote off the debt.
"""

import inspect

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.events import irrigation_planner as H


class TestSettingsDict(FrappeTestCase):
	def test_exposes_every_key_the_engine_reads(self):
		s = H.settings_dict()
		for key in (
			"et_crop_coefficient",
			"default_application_rate_mm_hr",
			"default_irrigation_coverage",
			"min_weather_days",
			"auto_cycle_threshold_hrs",
			"cycles_when_above_threshold",
			"cycles_when_below_threshold",
			"cycle_rest_hours",
		):
			self.assertIn(key, s)

	def test_min_weather_days_is_a_usable_positive_int(self):
		self.assertGreaterEqual(H.settings_dict()["min_weather_days"], 1)


class TestHookOwnsDemandOnly(FrappeTestCase):
	def test_the_hook_never_assigns_allocation_fields(self):
		src = inspect.getsource(H.compute_shift)
		for field in (
			"doc.shift_hours",
			"doc.cycles_count",
			"doc.cycle_hours_each",
			"doc.cycle_plan",
			"doc.scheduled_start",
			"doc.scheduled_end",
			"doc.delivered_depth_mm",
			"doc.unmet_deficit_mm",
			"doc.capacity_warning",
		):
			self.assertNotIn(field, src, f"{field} belongs to the scheduler, not the hook")

	def test_the_hook_writes_the_measured_window(self):
		src = inspect.getsource(H.compute_shift)
		self.assertIn("doc.measured_from", src)
		self.assertIn("doc.measured_to", src)
		self.assertIn("doc.required_hours", src)


class TestCarriedForward(FrappeTestCase):
	def test_no_prior_planner_carries_nothing(self):
		self.assertEqual(carried := H.carried_for("__nofarm__", "__noblock__", "2026-07-16"), 0.0)
		self.assertIsInstance(carried, float)

	def test_carry_comes_from_the_most_recent_prior_week_not_an_exact_date(self):
		rows = frappe.get_all(
			"Irrigation Planner",
			filters={"docstatus": ["<", 2]},
			fields=["farm", "block", "to_date", "unmet_deficit_mm"],
			order_by="to_date desc",
			limit=1,
		)
		if not rows:
			self.skipTest("no planners on this site")
		r = rows[0]
		# 30 days past the last planner's week: the old exact-date lookup returned
		# nothing here and reset the debt.
		far_future = frappe.utils.add_days(r["to_date"], 30)
		self.assertAlmostEqual(
			H.carried_for(r["farm"], r["block"], far_future),
			float(r["unmet_deficit_mm"] or 0),
			places=3,
		)


class TestPersistentWeeks(FrappeTestCase):
	def test_no_history_is_no_streak(self):
		self.assertEqual(H.persistent_weeks("__nofarm__", "__noblock__", "2026-07-16"), 0)

	def test_streak_never_exceeds_the_threshold_it_is_compared_against(self):
		rows = frappe.get_all(
			"Irrigation Planner",
			filters={"docstatus": ["<", 2]},
			fields=["farm", "block", "to_date"],
			order_by="to_date desc",
			limit=1,
		)
		if not rows:
			self.skipTest("no planners on this site")
		r = rows[0]
		streak = H.persistent_weeks(r["farm"], r["block"], frappe.utils.add_days(r["to_date"], 30))
		self.assertLessEqual(streak, H.CHRONIC_WEEKS_THRESHOLD)
