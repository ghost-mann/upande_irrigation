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
