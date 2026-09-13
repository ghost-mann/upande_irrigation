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

	def test_pump_defaults_give_a_real_capacity_not_a_full_week(self):
		from upande_irrigation.engine import allocate as A

		hours, source = A.pump_capacity_hours(None, H.pump_defaults(H.settings_dict()))
		self.assertEqual(source, A.SOURCE_DEFAULT)
		self.assertLess(hours, A.FULL_WEEK_HOURS)
		self.assertGreater(hours, 0)


class TestShiftSettings(FrappeTestCase):
	"""Per-shift rate and coverage: identical by default, overridable per shift.

	Bug locked out: the removed per-block table offered these two fields but never
	fed them into the calculation, so editing them changed nothing.
	"""

	BASE = {"default_application_rate_mm_hr": 2.8, "default_irrigation_coverage": 70.0}

	def test_no_block_returns_the_farm_defaults_unchanged(self):
		self.assertEqual(H.shift_settings(self.BASE, None), self.BASE)

	def test_a_shift_with_no_overrides_inherits(self):
		from upande_irrigation import shifts
		names = shifts.active_shifts("Lokitela", "70HA")
		block = names[0] if names else None
		if not block:
			self.skipTest("no active shifts on this site")

		# First prove the override is actually used, with a value distinct from the
		# farm default — only then clear it and check the farm default comes back.
		# The two figures below must differ, or a fallback to zero and a fallback to
		# the farm default would be indistinguishable.
		sched = frappe.get_single("Irrigation Scheduler")
		for row in sched.shift_blocks:
			if row.shift == block:
				row.application_rate_mm_hr = 5.5
				row.irrigation_coverage = 60.0
		sched.save(ignore_permissions=True)
		overridden = H.shift_settings(self.BASE, block)
		self.assertAlmostEqual(overridden["default_application_rate_mm_hr"], 5.5)
		self.assertAlmostEqual(overridden["default_irrigation_coverage"], 60.0)
		self.assertNotAlmostEqual(
			overridden["default_application_rate_mm_hr"], self.BASE["default_application_rate_mm_hr"]
		)

		sched.reload()
		for row in sched.shift_blocks:
			if row.shift == block:
				row.application_rate_mm_hr = 0
				row.irrigation_coverage = 0
		sched.save(ignore_permissions=True)
		out = H.shift_settings(self.BASE, block)
		self.assertAlmostEqual(out["default_application_rate_mm_hr"], 2.8)
		self.assertAlmostEqual(out["default_irrigation_coverage"], 70.0)

	def test_a_shift_override_reaches_the_calculation(self):
		from upande_irrigation import shifts
		names = shifts.active_shifts("Lokitela", "70HA")
		block = names[0] if names else None
		if not block:
			self.skipTest("no active shifts on this site")
		sched = frappe.get_single("Irrigation Scheduler")
		for row in sched.shift_blocks:
			if row.shift == block:
				row.application_rate_mm_hr = 4.0
				row.irrigation_coverage = 90.0
		sched.save(ignore_permissions=True)
		out = H.shift_settings(self.BASE, block)
		self.assertAlmostEqual(out["default_application_rate_mm_hr"], 4.0)
		self.assertAlmostEqual(out["default_irrigation_coverage"], 90.0)

		# And the override must actually change the hours, which is what the old
		# per-block fields failed to do.
		from upande_irrigation.engine import demand as D

		agg = {"et_crop_mm": 40.0, "rainfall_mm": 0.0, "days": 7, "temp_days": 7, "mean_temp": 20.0}
		base = D.demand(agg, 0.0, {**self.BASE, "et_crop_coefficient": 0.65})
		over = D.demand(agg, 0.0, {**out, "et_crop_coefficient": 0.65})
		self.assertNotAlmostEqual(base["required_hours"], over["required_hours"], places=3)
		self.assertLess(over["required_hours"], base["required_hours"])

	def test_the_source_of_a_shifts_figures_is_recorded_on_the_planner(self):
		import inspect

		src = inspect.getsource(H.compute_shift)
		self.assertIn("doc.applied_rate_mm_hr", src)
		self.assertIn("doc.applied_coverage_pct", src)
		self.assertIn("shift_settings(", src)


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
