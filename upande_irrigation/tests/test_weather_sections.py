"""The Irrigation Sections table on the Weather view.

Meniscus's version read area/valves/density/output/coverage/soil fields that no
schema on either site has, so every agronomic cell rendered "—". This one is
built from data that exists: the shift→block mapping (Irrigation Shift Block,
on the Irrigation Scheduler single) and the valves in Tank And Valve.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.api import weather
from upande_irrigation.tests.helpers import NoCommit, make_farm, make_warehouse

FARM = "_Test Sections Farm"


def _shift_row(shift, block, farm, active=1, rate=None, coverage=None):
	frappe.get_doc({
		"doctype": "Irrigation Shift Block",
		"parent": "Irrigation Scheduler", "parenttype": "Irrigation Scheduler", "parentfield": "shift_blocks",
		"shift": shift, "block": block, "farm": farm, "is_active": active,
		"application_rate_mm_hr": rate, "irrigation_coverage": coverage,
	}).db_insert()


class TestSections(NoCommit, FrappeTestCase):
	def setUp(self):
		make_farm(FARM)
		self.section = make_warehouse("_Test Sec 10HA", is_group=1, farm=FARM)
		self.b1 = make_warehouse("_Test Sec Blk 1", parent=self.section, farm=FARM)
		self.b2 = make_warehouse("_Test Sec Blk 2", parent=self.section, farm=FARM)
		_shift_row("10HA - SHIFT 1", self.b1, FARM, rate=4.0, coverage=80)
		_shift_row("10HA - SHIFT 1", self.b2, FARM, rate=4.0, coverage=80)
		_shift_row("10HA - SHIFT 2", self.b2, FARM, active=0, rate=6.0, coverage=60)
		for label, block in (("_TS V1", self.b1), ("_TS V2", self.b1), ("_TS V3", self.b2)):
			if not frappe.db.exists("Tank And Valve", {"asset_label": label}):
				frappe.get_doc({"doctype": "Tank And Valve", "asset_type": "Valve", "asset_label": label,
					"farm": FARM, "block": block}).insert(ignore_permissions=True)

	def row(self, farm=FARM):
		rows = [r for r in weather.sections(farm=farm)["sections"] if r["section"] == self.section]
		self.assertEqual(len(rows), 1)
		return rows[0]

	def test_blocks_shifts_and_valves_are_counted_once(self):
		r = self.row()
		self.assertEqual(r["blocks"], 2)
		self.assertEqual(r["shifts"], 2)
		self.assertEqual(r["valves"], 3)

	def test_rate_and_coverage_average_the_shift_overrides(self):
		r = self.row()
		self.assertAlmostEqual(r["application_rate_mm_hr"], 5.0)
		self.assertAlmostEqual(r["coverage_pct"], 70.0)

	def test_a_section_with_any_active_shift_is_active(self):
		self.assertTrue(self.row()["active"])

	def test_farm_filter_excludes_other_farms(self):
		others = weather.sections(farm="_No Such Farm")["sections"]
		self.assertFalse([r for r in others if r["section"] == self.section])
