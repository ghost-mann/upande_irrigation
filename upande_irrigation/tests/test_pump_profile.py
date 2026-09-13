"""Tests for the adopted pump profile.

The bug being locked out: allocate.py, resources.py and irrigation_planner.py all
query Irrigation Pump Profile, which belongs to upande_kaitet — an app the v16
destination does not have. frappe.db.count on a missing DocType raises, so the
whole scheduler run dies rather than falling back to the farm defaults it was
written to fall back to.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

DOCTYPE = "Irrigation Pump Profile"


class TestPumpProfileIsOurs(FrappeTestCase):
	def test_the_doctype_exists(self):
		self.assertTrue(frappe.db.exists("DocType", DOCTYPE))

	def test_it_belongs_to_upande_irrigation(self):
		self.assertEqual(frappe.db.get_value("DocType", DOCTYPE, "module"), "Upande Irrigation")

	def test_it_is_not_custom(self):
		self.assertEqual(frappe.db.get_value("DocType", DOCTYPE, "custom"), 0)

	def test_counting_it_does_not_raise(self):
		self.assertIsInstance(frappe.db.count(DOCTYPE), int)

	def test_the_section_link_points_at_warehouse(self):
		meta = frappe.get_meta(DOCTYPE)
		self.assertEqual(meta.get_field("irrigation_section").options, "Warehouse")

	def test_flow_rate_defaults_to_the_estate_assumption(self):
		meta = frappe.get_meta(DOCTYPE)
		self.assertEqual(float(meta.get_field("pump_flow_rate_m3_per_hr").default), 25.0)
