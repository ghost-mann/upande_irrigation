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


class TestWaterSource(FrappeTestCase):
	"""Locks out: the river intake treated as a section pump (it would get a cycle
	queue), two intakes on one farm, and profiles saved before the role existed
	(NULL role and source) dropping out of the run sheet."""

	FARM = "_Test Water Source Farm"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from upande_irrigation.tests.helpers import make_farm, make_warehouse

		make_farm(cls.FARM)
		cls.res = make_warehouse("_Test WS Reservoir Section", is_group=1, farm=cls.FARM)
		cls.river = make_warehouse("_Test WS River Section", is_group=1, farm=cls.FARM)

	def setUp(self):
		for name in frappe.get_all(DOCTYPE, filters={"farm": self.FARM}, pluck="name"):
			frappe.delete_doc(DOCTYPE, name, force=True, ignore_permissions=True)

	def pump(self, **kw):
		return frappe.get_doc({"doctype": DOCTYPE, "farm": self.FARM, "pump_flow_rate_m3_per_hr": 50, **kw}).insert(ignore_permissions=True)

	def test_a_section_pump_needs_a_section(self):
		with self.assertRaises(frappe.ValidationError):
			self.pump(pump_role="Section pump")

	def test_one_intake_per_farm(self):
		self.pump(pump_role="Reservoir intake", pump_name="_WS Intake")
		with self.assertRaises(frappe.ValidationError):
			self.pump(pump_role="Reservoir intake", pump_name="_WS Intake 2")

	def test_pumps_excludes_the_intake_and_carries_the_source(self):
		from upande_irrigation.api import runsheet as RS

		self.pump(pump_role="Reservoir intake", pump_name="_WS Intake",
		          run_windows=[{"day": "Every day", "start_time": "00:00:00", "end_time": "12:00:00"}])
		self.pump(irrigation_section=self.res, pump_name="_WS Res Pump")
		self.pump(irrigation_section=self.river, pump_name="_WS River Pump", water_source="River direct")
		got = RS.pumps(self.FARM)
		self.assertEqual(set(got), {self.res, self.river})
		self.assertEqual(got[self.res]["source"], "Reservoir")
		self.assertEqual(got[self.river]["source"], "River direct")
		ip = RS.intake(self.FARM)
		self.assertEqual(ip["pump"], "_WS Intake")
		self.assertEqual(ip["flow"], 50)
		self.assertEqual(len(ip["windows"]), 1)

	def test_a_profile_from_before_the_role_is_a_reservoir_section_pump(self):
		from upande_irrigation.api import runsheet as RS

		p = self.pump(irrigation_section=self.res, pump_name="_WS Old Pump")
		frappe.db.set_value(DOCTYPE, p.name, {"pump_role": None, "water_source": None})
		self.assertEqual(RS.pumps(self.FARM)[self.res]["source"], "Reservoir")
		self.assertIsNone(RS.intake(self.FARM))
