"""The gate on the production cutover.

Everything below has to hold on real migrated data before the same path is run
against upande.com. A green suite with an empty database proves nothing, so each
assertion names the volume it expects.

Two exceptions to "assert exact volumes", both deliberate:

- `Irrigation Scheduler Run` is asserted as `>= 32`, not `== 32`. This gate's own
  Step 3 runs the live scheduler, which always writes one more run record whether
  or not it plans anything — a fixed exact count and a mandatory live run cannot
  both hold at once. Every other doctype in this file stays an exact equality.
- A scheduler run that attempts zero shifts is not, by itself, a defect. The
  scheduler is designed to refuse to plan and flag when the weather window is
  incomplete, rather than silently returning zero. On this site the newest
  Weather Reading is far older than any live run's planning window, so a
  0-attempt run is the CORRECT, designed behaviour here — and would be a real
  defect if the weather were fresh. Do not "fix" a future 0-attempt run by
  loosening this further or by manufacturing weather data; fix the weather
  data, or accept the gap and say so.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation import shifts

FARM = "Lokitela"


class TestMigratedSiteIsWhole(FrappeTestCase):
	def test_every_record_arrived(self):
		expected = {
			"Weather Reading": 5431, "Irrigation Planner": 1346,
			"Reservoir Pumping Record": 369, "Water Transfer": 279,
			"Tank And Valve": 58,
		}
		for doctype, count in expected.items():
			self.assertEqual(frappe.db.count(doctype), count, f"{doctype} short")

		# Not an exact match like the five above: this same gate's Step 3 runs
		# the live scheduler, which writes one more Irrigation Scheduler Run
		# record every time it runs, whether or not it plans anything. 32 is
		# the migrated floor, not a ceiling this gate can hold itself to.
		self.assertGreaterEqual(
			frappe.db.count("Irrigation Scheduler Run"), 32, "Irrigation Scheduler Run short"
		)

	def test_the_latest_scheduler_run_is_healthy(self):
		"""A live run must never fail a planner, and must explain a zero-attempt run.

		On this site the newest Weather Reading is 2026-07-24, well outside any
		current planning window, so `shifts_attempted == 0` on the latest run is
		expected and CORRECT — the engine's designed behaviour is to refuse to
		plan and flag when weather data is incomplete, never to silently return
		zero. The same zero on a site with fresh weather would be a real defect.
		This test asserts health (no failures, and a stated reason when nothing
		was attempted), not volume — it must not be loosened further, and a
		future 0-attempt run must not be "fixed" by fabricating weather data.
		"""
		run = frappe.get_last_doc("Irrigation Scheduler Run")
		self.assertIn(run.status, ("Success", "Partial"), run.status)
		self.assertEqual(run.planners_failed, 0)
		if run.shifts_attempted == 0:
			explanation = f"{run.summary or ''} {run.error_summary or ''}".lower()
			self.assertTrue(
				any(word in explanation for word in ("weather", "reading")),
				"a 0-attempt run must say why, not fail silently",
			)

	def test_no_doctype_is_still_custom(self):
		custom = frappe.get_all(
			"DocType", filters={"module": "Upande Irrigation", "custom": 1}, pluck="name"
		)
		self.assertEqual(custom, [])

	def test_the_retired_doctypes_are_gone(self):
		for dt in ("Block Type", "Block configaration", "Blocks List"):
			self.assertFalse(frappe.db.exists("DocType", dt))

	def test_the_only_orphaned_shift_is_the_one_we_excluded(self):
		"""Planners orphaned by the 23HA - SHIFT 1 exclusion are expected, and bounded.

		Both mapping rows for that shift named a group warehouse, so both were
		excluded and the shift no longer exists. Its planners are orphaned by
		design — reported, not repaired. Any OTHER orphan is a real regression.
		"""
		known = {r.shift for r in frappe.get_single("Irrigation Scheduler").shift_blocks}
		used = {b for b in frappe.get_all("Irrigation Planner", pluck="block") if b}
		self.assertEqual(used - known, {"23HA - SHIFT 1"})

	def test_every_shift_block_is_a_real_leaf_warehouse(self):
		for row in frappe.get_single("Irrigation Scheduler").shift_blocks:
			self.assertTrue(frappe.db.exists("Warehouse", row.block), f"{row.block} missing")
			self.assertFalse(
				frappe.db.get_value("Warehouse", row.block, "is_group"),
				f"{row.block} is a group",
			)

	def test_the_four_sections_each_report_active_shifts(self):
		for prefix in ("23HA", "56HA", "65HA", "70HA"):
			self.assertGreater(len(shifts.active_shifts(FARM, prefix)), 0, prefix)
