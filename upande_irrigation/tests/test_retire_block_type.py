"""Tests for retiring Block Type.

The bug being locked out: dropping the DocType while Irrigation Planner.block is
still a Link to it leaves 1,346 records pointing at nothing, and Frappe will
happily render a broken link rather than error. The field has to become Data
first, keeping its values byte-for-byte.
"""

import frappe
from frappe.tests.utils import FrappeTestCase


class TestBlockTypeRetired(FrappeTestCase):
	def test_planner_block_is_now_data(self):
		self.assertEqual(frappe.get_meta("Irrigation Planner").get_field("block").fieldtype, "Data")

	def test_the_three_doctypes_are_gone(self):
		for dt in ("Block Type", "Block configaration", "Blocks List"):
			self.assertFalse(frappe.db.exists("DocType", dt), f"{dt} still present")

	def test_planner_block_values_survived(self):
		blanks = frappe.db.count("Irrigation Planner", {"block": ["in", ["", None]]})
		self.assertEqual(blanks, 0, "planners lost their shift label")

	def test_the_only_orphaned_shift_is_the_one_we_excluded(self):
		"""23HA - SHIFT 1 is gone, and planners still point at it. That is reported, not repaired.

		Both of that shift's mapping rows named 23HA_SECTION - KL, a group warehouse,
		so both were excluded and the shift ceased to exist. The planners that
		referenced it are now orphaned. Asserting the orphan set is EMPTY would fail
		on correct data; asserting it is exactly this one shift still catches any NEW
		orphan a later change introduces.
		"""
		known = {r.shift for r in frappe.get_single("Irrigation Scheduler").shift_blocks}
		used = {b for b in frappe.get_all("Irrigation Planner", pluck="block") if b}
		self.assertEqual(
			used - known,
			{"23HA - SHIFT 1"},
			"orphaned planner shifts changed — expected only the excluded 23HA - SHIFT 1",
		)
