"""The gate on the production cutover.

Everything below has to hold on real migrated data before the same path is run
against upande.com. A green suite with an empty database proves nothing, so each
assertion names the volume it expects.
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
			"Tank And Valve": 58, "Irrigation Scheduler Run": 32,
		}
		for doctype, count in expected.items():
			self.assertEqual(frappe.db.count(doctype), count, f"{doctype} short")

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
