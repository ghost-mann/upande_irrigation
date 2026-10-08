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
		# neither shift is re-planned: its rows are exactly the ones it had
		for shift in (first.shift, skipped.shift):
			self.assertEqual({r.name for r in rows if r.shift == shift}, {r.name for r in sheet.runs if r.shift == shift})

	def test_a_started_shift_keeps_its_remaining_cycles(self):
		RS.generate(FARM, DAY)
		sheet = self.sheet()
		multi = [r for r in sheet.runs if (r.cycles or 1) > 1]
		if not multi:
			self.skipTest("no multi-cycle shift in this fixture")
		first = next(r for r in multi if r.cycle_no == 1)
		siblings = {r.name for r in sheet.runs if r.shift == first.shift}
		RS.set_status(first.name, "Done")
		RS.generate(FARM, DAY)
		self.assertEqual({r.name for r in self.sheet().runs if r.shift == first.shift}, siblings)

	def test_regeneration_never_places_on_top_of_kept_cycles(self):
		RS.generate(FARM, DAY)
		first = self.sheet().runs[0]
		RS.set_status(first.name, "Running")
		RS.generate(FARM, DAY)
		# Skipped cycles free their slot; only cycles that use the pump count.
		spans = sorted((frappe.utils.get_datetime(r.planned_start), frappe.utils.get_datetime(r.planned_end))
		               for r in self.sheet().runs if r.planned_start and r.pump == first.pump
		               and r.status in ("Planned", "Running", "Done", "Partial"))
		for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
			self.assertLessEqual(a1, b0)

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

	def test_no_intake_means_supply_not_checked(self):
		RS.generate(FARM, DAY)
		sheet = self.sheet()
		self.assertFalse(sheet.reservoir_refill_m3)
		self.assertIn("not checked", sheet.supply_warning)
		self.assertGreater(sheet.reservoir_draw_m3, 0)

	def test_a_small_intake_warns_but_keeps_every_cycle(self):
		RS.generate(FARM, DAY)
		before = [(r.shift, r.cycle_no, r.status) for r in self.sheet().runs]
		intake = frappe.get_doc({"doctype": "Irrigation Pump Profile", "farm": FARM, "pump_role": "Reservoir intake",
		                         "pump_name": "_TRS Intake", "pump_flow_rate_m3_per_hr": 1,
		                         "run_windows": [{"day": "Every day", "start_time": "06:00:00", "end_time": "07:00:00"}]}).insert(ignore_permissions=True)
		try:
			RS.generate(FARM, DAY)
			sheet = self.sheet()
			self.assertEqual(sheet.reservoir_refill_m3, 1)
			self.assertIn("exceeds", sheet.supply_warning)
			self.assertEqual([(r.shift, r.cycle_no, r.status) for r in sheet.runs], before)
			self.assertIn("exceeds", RS.today(FARM, DAY)["sheets"][0]["supply_warning"])
		finally:
			frappe.delete_doc("Irrigation Pump Profile", intake.name, force=True, ignore_permissions=True)

	def test_reservoir_budget_counts_only_reservoir_fed_meters(self):
		river = make_warehouse("_Test RS River Section", is_group=1, farm=FARM)
		frappe.get_doc({"doctype": "Irrigation Pump Profile", "farm": FARM, "irrigation_section": river, "pump_name": "_TRS River Pump",
		                "water_source": "River direct", "pump_flow_rate_m3_per_hr": 80}).insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "Reservoir Pumping Record", "farm": FARM, "date": DAY, "volume_of_water_used_m3": 1000,
		                "docstatus": 1}).db_insert()
		for section, m3 in ((self.section, 600), (river, 300)):
			frappe.get_doc({"doctype": "Water Meter Reading", "irrigation_section": section, "date": f"{DAY} 08:00:00",
			                "units_used": m3, "docstatus": 1}).db_insert()
		monday = frappe.utils.add_days(DAY, -frappe.utils.getdate(DAY).weekday())
		rows = RS.reservoir_budget(FARM, start=monday)["rows"]
		row = next(r for r in rows if r["week"] == str(monday))
		self.assertEqual((row["intake_m3"], row["drawn_m3"], row["loss_m3"], row["loss_pct"]), (1000, 600, 400, 40))

	def intake(self, flow=1, windows=True):
		return frappe.get_doc({"doctype": "Irrigation Pump Profile", "farm": FARM, "pump_role": "Reservoir intake",
		                       "pump_name": "_TRS Intake", "pump_flow_rate_m3_per_hr": flow,
		                       "run_windows": [{"day": "Every day", "start_time": "06:00:00", "end_time": "07:00:00"}] if windows else []}
		                      ).insert(ignore_permissions=True)

	def test_an_intake_without_windows_is_not_checked_rather_than_warned(self):
		ip = self.intake(flow=500, windows=False)
		try:
			RS.generate(FARM, DAY)
			sheet = self.sheet()
			self.assertNotIn("exceeds", sheet.supply_warning)
			self.assertIn("no run windows", sheet.supply_warning)
		finally:
			frappe.delete_doc("Irrigation Pump Profile", ip.name, force=True, ignore_permissions=True)

	def test_skipping_cycles_clears_the_supply_warning(self):
		ip = self.intake(flow=1)
		try:
			RS.generate(FARM, DAY)
			self.assertIn("exceeds", self.sheet().supply_warning)
			for r in self.sheet().runs:
				if r.status in ("Planned", "Running"):  # earlier tests in this class may leave one Running
					RS.set_status(r.name, "Skipped", skip_reason="reservoir low")
			sheet = self.sheet()
			self.assertEqual(sheet.reservoir_draw_m3, 0, [(r.shift, r.status, r.planned_hours) for r in sheet.runs])
			self.assertNotIn("exceeds", sheet.supply_warning)
		finally:
			frappe.delete_doc("Irrigation Pump Profile", ip.name, force=True, ignore_permissions=True)
