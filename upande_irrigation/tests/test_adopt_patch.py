"""Tests for the custom-to-code adoption patch.

The bug being locked out: flipping custom to 0 while the DocType still has its
Custom Field rows would make bench migrate drop columns the fixture-era rows still
use. The patch must delete the Custom Field rows it has already folded into the
code DocType, and it must be safe to run twice.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.patches.v1_0.adopt_custom_doctypes_as_code import MANAGED, execute


class TestAdoptPatch(FrappeTestCase):
	def test_it_manages_exactly_the_nine_converted_doctypes(self):
		self.assertEqual(len(MANAGED), 9)
		self.assertIn("Irrigation Planner", MANAGED)
		self.assertNotIn("Block Type", MANAGED)
		self.assertNotIn("Tank And Valve", MANAGED)

	def test_every_managed_doctype_ends_up_not_custom(self):
		execute()
		for dt in MANAGED:
			if frappe.db.exists("DocType", dt):
				self.assertEqual(
					frappe.db.get_value("DocType", dt, "custom"), 0, f"{dt} still custom"
				)

	def test_no_custom_fields_remain_on_managed_doctypes(self):
		execute()
		left = frappe.get_all("Custom Field", filters={"dt": ["in", list(MANAGED)]}, pluck="name")
		self.assertEqual(left, [])

	def test_running_twice_is_harmless(self):
		execute()
		execute()
