"""Tests for the shift/block child table.

Two bugs being locked out: a row pointing at a group warehouse — which is what the
three bad 23HA rows on the live site are, they name 23HA_SECTION rather than a
block — and losing the per-shift rate and coverage overrides, which shift_settings()
reads and which commit 22fc836 added precisely because every shift was otherwise
computing identical hours.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

DOCTYPE = "Irrigation Shift Block"


class TestShiftBlockSchema(FrappeTestCase):
	def test_it_is_a_child_table(self):
		self.assertEqual(frappe.db.get_value("DocType", DOCTYPE, "istable"), 1)

	def test_block_links_to_warehouse(self):
		self.assertEqual(frappe.get_meta(DOCTYPE).get_field("block").options, "Warehouse")

	def test_it_carries_the_shift_overrides(self):
		meta = frappe.get_meta(DOCTYPE)
		for fieldname in ("application_rate_mm_hr", "irrigation_coverage"):
			self.assertIsNotNone(meta.get_field(fieldname), f"{fieldname} missing")

	def test_the_scheduler_single_holds_the_table(self):
		field = frappe.get_meta("Irrigation Scheduler").get_field("shift_blocks")
		self.assertIsNotNone(field, "Irrigation Scheduler has no shift_blocks table")
		self.assertEqual(field.options, DOCTYPE)


_ROW_META_KEYS = (
	"name", "creation", "modified", "modified_by", "owner",
	"parent", "parentfield", "parenttype", "doctype", "idx",
)


class TestShiftBlockValidateHook(FrappeTestCase):
	"""Exercises the real path: Irrigation Scheduler.validate(), not the child in isolation.

	Frappe never calls a child row's own validate() during a parent save, so the
	group-warehouse rejection must also be wired as a doc_events hook on the
	Irrigation Scheduler Single, or the three real 23HA_SECTION - KL rows would
	migrate silently broken.
	"""

	def setUp(self):
		self.original_shift_blocks = [
			{k: v for k, v in row.as_dict().items() if k not in _ROW_META_KEYS}
			for row in frappe.get_single("Irrigation Scheduler").shift_blocks
		]

	def tearDown(self):
		doc = frappe.get_doc("Irrigation Scheduler")
		doc.set("shift_blocks", [])
		for row in self.original_shift_blocks:
			doc.append("shift_blocks", row)
		doc.save()

	def test_group_warehouse_rejected_on_scheduler_save(self):
		group_warehouses = frappe.get_all("Warehouse", filters={"is_group": 1}, pluck="name", limit=1)
		self.assertTrue(group_warehouses, "No group warehouse found on this site to test against")

		doc = frappe.get_doc("Irrigation Scheduler")
		doc.append("shift_blocks", {"shift": "TEST SHIFT - GROUP", "block": group_warehouses[0]})
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_leaf_warehouse_saves_fine(self):
		leaf_warehouses = frappe.get_all("Warehouse", filters={"is_group": 0}, pluck="name", limit=1)
		self.assertTrue(leaf_warehouses, "No leaf warehouse found on this site to test against")

		doc = frappe.get_doc("Irrigation Scheduler")
		doc.append("shift_blocks", {"shift": "TEST SHIFT - LEAF", "block": leaf_warehouses[0]})
		doc.save()
