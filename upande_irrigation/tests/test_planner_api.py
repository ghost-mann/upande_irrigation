"""Tests for the Planning read model.

Two bugs locked out:

  · millimetres were summed across shifts, so a farm of 55 shifts each owing
    89 mm reported "4,900 mm" — depth is per unit area and does not add;
  · shifts were ordered as strings, so Shift 10 sorted before Shift 2 and the
    Gantt rows did not read in the sequence the section runs.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.api import planner as P


class TestCycleWindows(FrappeTestCase):
	def test_no_start_means_no_windows(self):
		self.assertEqual(P.cycle_windows(None, 3, 2.0, 2.0), [])

	def test_no_cycles_means_no_windows(self):
		self.assertEqual(P.cycle_windows("2026-07-02 00:00:00", 0, 2.0, 2.0), [])

	def test_windows_are_separated_by_the_rest_interval(self):
		w = P.cycle_windows("2026-07-02 00:00:00", 3, 2.0, 2.0)
		self.assertEqual(len(w), 3)
		gap = (w[1][0] - w[0][1]).total_seconds() / 3600.0
		self.assertAlmostEqual(gap, 2.0)

	def test_each_window_is_the_cycle_length(self):
		for start, end in P.cycle_windows("2026-07-02 00:00:00", 2, 3.5, 1.0):
			self.assertAlmostEqual((end - start).total_seconds() / 3600.0, 3.5)


class TestFetch(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.data = P.fetch()

	def test_the_week_is_seven_days(self):
		w = self.data["week"]
		self.assertEqual(frappe.utils.date_diff(w["to_date"], w["from_date"]), 6)

	def test_depths_are_per_shift_not_summed(self):
		# A farm-wide depth above a few hundred mm in one week is not physical; it is
		# the signature of summing millimetres across shifts.
		w = self.data["week"]
		if not w["planners"]:
			self.skipTest("no planners for the default week")
		self.assertLess(w["demand_mm_per_shift"], 1000)
		self.assertLessEqual(w["delivered_mm_per_shift"], w["demand_mm_per_shift"] + 1e-6)
		self.assertEqual(self.data["balance"]["unit"], "mm per shift")
		for v in self.data["balance"]["demand"]:
			self.assertLess(v, 1000)

	def test_shifts_are_ordered_numerically(self):
		for sec in self.data["gantt"]["sections"]:
			nums = [int(s["n"]) for s in sec["shifts"] if s["n"].isdigit()]
			self.assertEqual(nums, sorted(nums), f"{sec['label']} is out of order")

	def test_hours_do_add_and_granted_never_exceeds_required(self):
		w = self.data["week"]
		if not w["planners"]:
			self.skipTest("no planners for the default week")
		self.assertLessEqual(w["granted_hours"], w["required_hours"] + 0.5)

	def test_no_pump_is_charged_more_than_the_week_holds(self):
		for p in self.data["pump_load"]["pumps"]:
			self.assertLessEqual(len(p["days"]), 7)
			# Rounding on cycle boundaries can nudge a full day a hair over 24 h.
			for h in p["days"]:
				self.assertLessEqual(h, 24.1)

	def test_a_week_with_no_planners_still_returns_a_usable_shape(self):
		out = P.fetch(week="1990-01-04")
		self.assertEqual(out["week"]["planners"], 0)
		self.assertEqual(out["gantt"]["sections"], [])
		self.assertEqual(out["pump_load"]["pumps"], [])
		self.assertEqual(len(out["days"]), 7)


class TestExplain(FrappeTestCase):
	def test_a_missing_planner_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			P.explain("__nope__")

	def test_no_planner_argument_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			P.explain("")

	def test_the_trace_ends_where_the_planner_says_it_does(self):
		row = frappe.get_all(
			"Irrigation Planner",
			filters={"docstatus": ["<", 2], "required_hours": [">", 0]},
			fields=["name", "shift_hours", "unmet_deficit_mm"],
			limit=1,
		)
		if not row:
			self.skipTest("no planner with demand on this site")
		out = P.explain(row[0]["name"])
		steps = {s["key"]: s for s in out["steps"]}
		# Read from the document, never recomputed — so the explainer cannot drift
		# from what the planner actually holds.
		self.assertAlmostEqual(steps["granted"]["value"], float(row[0]["shift_hours"] or 0), places=2)
		self.assertAlmostEqual(steps["unmet"]["value"], float(row[0]["unmet_deficit_mm"] or 0), places=2)

	def test_the_trace_covers_every_stage_in_order(self):
		row = frappe.get_all(
			"Irrigation Planner", filters={"docstatus": ["<", 2]}, fields=["name"], limit=1
		)
		if not row:
			self.skipTest("no planners on this site")
		keys = [s["key"] for s in P.explain(row[0]["name"])["steps"]]
		self.assertEqual(
			keys,
			[
				"measure", "et_crop", "clean", "deficit", "carried", "total",
				"required", "granted", "cycles", "delivered", "unmet",
			],
		)
