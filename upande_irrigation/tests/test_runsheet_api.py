"""Run sheet generation and the operator's ticks, end to end on real doctypes.

Locks out: regenerating wiping cycles the operator already marked; re-planning a
shift that already ran today; a Partial without hours or a Skip without a reason;
a pump profile's windows ignored.
"""

import datetime

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation import setup
from upande_irrigation.api import runsheet as RS
from upande_irrigation.tests.helpers import NoCommit, make_farm, make_warehouse

FARM = "_Test RS Farm"
DAY = "2026-07-20"


class TestRunSheet(NoCommit, FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup.ensure_agronomy_defaults()
		frappe.db.set_single_value("Irrigation Settings", "balance_start_date", "2026-07-01")
		frappe.local._irr_agronomy = None
		make_farm(FARM)
		cls.section = make_warehouse("_Test RS Section", is_group=1, farm=FARM)
		cls.blocks = [make_warehouse(f"_Test RS Block {i}", parent=cls.section, farm=FARM) for i in (1, 2, 3)]
		for b in cls.blocks:
			frappe.get_doc({"doctype": "Irrigation Block Profile", "block": b, "area_ha": 2, "tree_count": 500}).insert(ignore_permissions=True)
		start = frappe.utils.getdate("2026-07-01")
		for i in range(19):  # dry spell up to the 19th
			frappe.get_doc({"doctype": "Weather Reading", "farm": FARM, "date": start + datetime.timedelta(days=i),
			                "rainfall_mm": 0, "pan_cups": 14, "minimum_temperature": 12, "maximum_temperature": 29}).insert(ignore_permissions=True)
		for i, b in enumerate(cls.blocks):
			frappe.get_doc({"doctype": "Irrigation Shift Block", "parent": "Irrigation Scheduler", "parenttype": "Irrigation Scheduler",
			                "parentfield": "shift_blocks", "shift": f"_TRS - SHIFT {i + 1}", "block": b, "farm": FARM, "is_active": 1}).db_insert()
		frappe.get_doc({"doctype": "Irrigation Pump Profile", "farm": FARM, "irrigation_section": cls.section, "pump_name": "_TRS Pump",
		                "pump_flow_rate_m3_per_hr": 100,
		                "run_windows": [{"day": "Every day", "start_time": "06:00:00", "end_time": "14:00:00"}]}).insert(ignore_permissions=True)

	def sheet(self):
		return frappe.get_doc("Irrigation Run Sheet", frappe.db.get_value("Irrigation Run Sheet", {"farm": FARM, "date": DAY}))

	def test_generate_places_inside_the_pump_window(self):
		RS.generate(FARM, DAY)
		planned = [r for r in self.sheet().runs if r.status == "Planned"]
		self.assertTrue(planned)
		for r in planned:
			self.assertEqual(r.pump, "_TRS Pump")
			self.assertGreaterEqual(frappe.utils.get_datetime(r.planned_start), frappe.utils.get_datetime(f"{DAY} 06:00:00"))
			self.assertLessEqual(frappe.utils.get_datetime(r.planned_end), frappe.utils.get_datetime(f"{DAY} 14:00:00"))

	def test_regenerate_keeps_what_the_operator_did(self):
		RS.generate(FARM, DAY)
		sheet = self.sheet()
		first = sheet.runs[0]
		RS.set_status(first.name, "Done")
		skipped = next(r for r in sheet.runs if r.shift != first.shift)
		RS.set_status(skipped.name, "Skipped", skip_reason="pipe burst")
		RS.generate(FARM, DAY)
		rows = self.sheet().runs
		self.assertIn(("Done", first.shift), [(r.status, r.shift) for r in rows])
		self.assertIn(("Skipped", skipped.shift), [(r.status, r.shift) for r in rows])
		# neither shift is planned again the same day
		self.assertFalse([r for r in rows if r.shift in (first.shift, skipped.shift) and r.status in ("Planned", "Not placed")])

	def test_partial_needs_hours_and_skip_needs_a_reason(self):
		RS.generate(FARM, DAY)
		row = self.sheet().runs[0]
		with self.assertRaises(frappe.ValidationError):
			RS.set_status(row.name, "Partial")
		with self.assertRaises(frappe.ValidationError):
			RS.set_status(row.name, "Skipped", skip_reason=" ")
		RS.set_status(row.name, "Partial", actual_hours=1.5)
		self.assertEqual(frappe.db.get_value("Irrigation Run", row.name, "actual_hours"), 1.5)

	def test_today_and_week_views(self):
		RS.generate(FARM, DAY)
		t = RS.today(FARM, DAY)["sheets"][0]
		self.assertTrue(t["runs"])
		self.assertTrue(t["runs"][0]["blocks"])
		w = RS.week(FARM, DAY)["farms"][0]
		self.assertEqual(len(w["days"]), 7)
		self.assertTrue(any(c["due"] for s in w["shifts"] for c in s["cells"]))
		self.assertEqual(w["capacity"]["_TRS Pump"][DAY], 8.0)
