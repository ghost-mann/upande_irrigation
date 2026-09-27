"""Block profiles, seeding and the stored daily balance.

Locks out: a block with no emitter data dividing by zero; seeding overwriting a
calibrated value; the same water counted twice when both the valve log and the run
sheet record it; depletion escaping [0, TAW] on the real migrated weather.
"""

import datetime

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation import setup
from upande_irrigation.api import balance as BA
from upande_irrigation.tests.helpers import NoCommit, make_farm, make_warehouse

FARM = "_Test Balance Farm"


TUNABLES = ("area_ha", "tree_count", "planting_year", "canopy_cover_pct", "root_depth_m", "soil_texture",
            "depletion_fraction", "emitters_per_tree", "emitter_flow_lph", "application_efficiency")


def _profile(block, **kw):
	kw = {**{f: None for f in TUNABLES}, **kw}
	if frappe.db.exists("Irrigation Block Profile", block):
		doc = frappe.get_doc("Irrigation Block Profile", block)
		doc.update(kw)
		doc.save(ignore_permissions=True)
		return doc
	return frappe.get_doc({"doctype": "Irrigation Block Profile", "block": block, **kw}).insert(ignore_permissions=True)


class TestProfile(NoCommit, FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup.ensure_agronomy_defaults()
		make_farm(FARM)
		cls.section = make_warehouse("_Test Bal Section", is_group=1, farm=FARM)
		cls.block = make_warehouse("_Test Bal Block", parent=cls.section, farm=FARM)

	def test_derived_fields_use_defaults(self):
		p = _profile(self.block, area_ha=3.4, tree_count=864)
		self.assertAlmostEqual(p.application_rate_mm_hr, 1.779, places=2)  # 864 × 1 × 70 / 34 000
		self.assertAlmostEqual(p.taw_mm, 96.0)  # loam 160 mm/m × 0.6 m (10-year-old trees)
		self.assertAlmostEqual(p.raw_mm, 48.0)
		self.assertIn("soil_texture", p.using_defaults)

	def test_zero_area_gives_zero_rate_not_an_error(self):
		p = _profile(self.block, area_ha=0, tree_count=0)
		self.assertEqual(p.application_rate_mm_hr, 0)

	def test_block_values_override_defaults(self):
		p = _profile(self.block, area_ha=2, tree_count=500, soil_texture="Sandy loam", planting_year=frappe.utils.getdate().year - 1,
		             emitters_per_tree=2, emitter_flow_lph=40)
		self.assertAlmostEqual(p.application_rate_mm_hr, 2.0)  # 500 × 2 × 40 / 20 000
		self.assertAlmostEqual(p.taw_mm, 36.0)  # sandy loam 120 × 0.3 m (young tree)

	def test_seeding_never_overwrites(self):
		_profile(self.block, area_ha=9.99, tree_count=1)
		setup.seed_block_profiles()
		self.assertEqual(frappe.db.get_value("Irrigation Block Profile", self.block, "area_ha"), 9.99)


class TestRebuild(NoCommit, FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup.ensure_agronomy_defaults()
		make_farm(FARM)
		cls.section = make_warehouse("_Test Reb Section", is_group=1, farm=FARM)
		cls.block = make_warehouse("_Test Reb Block", parent=cls.section, farm=FARM)
		_profile(cls.block, area_ha=3.4, tree_count=864)
		cls.start = frappe.utils.getdate("2026-07-01")
		for i in range(10):
			frappe.get_doc({"doctype": "Weather Reading", "farm": FARM, "date": cls.start + datetime.timedelta(days=i),
			                "rainfall_mm": 0, "pan_cups": 10, "minimum_temperature": 12, "maximum_temperature": 28}).insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "Irrigation Shift Block", "parent": "Irrigation Scheduler", "parenttype": "Irrigation Scheduler",
		                "parentfield": "shift_blocks", "shift": "_TREB - SHIFT 1", "block": cls.block, "farm": FARM, "is_active": 1}).db_insert()
		cls.valve = frappe.get_doc({"doctype": "Tank And Valve", "asset_type": "Valve", "asset_label": "_TV REB", "farm": FARM,
		                            "block": cls.block}).insert(ignore_permissions=True).name

	def rows(self):
		return {str(r.date): r for r in frappe.get_all("Irrigation Block Balance", filters={"block": self.block}, fields=["*"])}

	def rebuild(self):
		return BA.rebuild(farm=FARM, start=self.start, end=self.start + datetime.timedelta(days=9))

	def test_dry_spell_accumulates_within_taw(self):
		out = self.rebuild()
		self.assertEqual(out["rows"], 10)
		last = self.rows()["2026-07-10"]
		self.assertAlmostEqual(last.depletion_mm, 10 * 5 * 0.75 * 0.75 * 0.85, places=1)
		self.assertLessEqual(last.depletion_mm, last.taw_mm)

	def test_a_done_run_sheet_cycle_counts(self):
		sheet = frappe.get_doc({"doctype": "Irrigation Run Sheet", "farm": FARM, "date": "2026-07-10", "runs": [
			{"shift": "_TREB - SHIFT 1", "status": "Done", "planned_hours": 10, "planned_start": "2026-07-10 06:00:00"}]}).insert(ignore_permissions=True)
		self.rebuild()
		r = self.rows()["2026-07-10"]
		self.assertEqual(r.irrigation_source, "Run Sheet")
		self.assertAlmostEqual(r.irrigation_mm, 10 * 1.779 * 0.9, places=0)
		frappe.delete_doc("Irrigation Run Sheet", sheet.name, force=True, ignore_permissions=True)

	def test_the_larger_record_counts_never_both(self):
		"""A 2 h test opening in the valve log must not hide a 10 h cycle ticked Done,
		and the two are never added together."""
		sheet = frappe.get_doc({"doctype": "Irrigation Run Sheet", "farm": FARM, "date": "2026-07-09", "runs": [
			{"shift": "_TREB - SHIFT 1", "status": "Done", "planned_hours": 10, "planned_start": "2026-07-09 06:00:00"}]}).insert(ignore_permissions=True)
		for at, state in (("2026-07-09 06:00:00", "Forced Open"), ("2026-07-09 08:00:00", "Auto")):
			frappe.get_doc({"doctype": "Valve Event", "valve": self.valve, "block": self.block, "at": at,
			                "to_state": state, "source": "Operator"}).insert(ignore_permissions=True)
		self.rebuild()
		r = self.rows()["2026-07-09"]
		self.assertEqual(r.irrigation_source, "Run Sheet")
		self.assertAlmostEqual(r.irrigation_mm, 10 * 1.779 * 0.9, places=0)  # 10 h, not 12
		frappe.delete_doc("Irrigation Run Sheet", sheet.name, force=True, ignore_permissions=True)
		frappe.db.delete("Valve Event", {"valve": self.valve})

	def test_a_longer_valve_log_wins(self):
		for at, state in (("2026-07-08 06:00:00", "Forced Open"), ("2026-07-08 12:00:00", "Auto")):
			frappe.get_doc({"doctype": "Valve Event", "valve": self.valve, "block": self.block, "at": at,
			                "to_state": state, "source": "Operator"}).insert(ignore_permissions=True)
		self.rebuild()
		r = self.rows()["2026-07-08"]
		self.assertEqual(r.irrigation_source, "Valve Log")
		self.assertAlmostEqual(r.irrigation_mm, 6 * 1.779 * 0.9, places=1)
		frappe.db.delete("Valve Event", {"valve": self.valve})

	def test_a_farm_with_no_weather_uses_the_fallback_not_zero(self):
		other = "_Test NoWx Farm"
		make_farm(other)
		sec = make_warehouse("_Test NoWx Section", is_group=1, farm=other)
		blk = make_warehouse("_Test NoWx Block", parent=sec, farm=other)
		_profile(blk, area_ha=1, tree_count=250)
		BA.rebuild(farm=other, start=self.start, end=self.start + datetime.timedelta(days=4))
		rows = frappe.get_all("Irrigation Block Balance", filters={"block": blk}, fields=["epan_mm", "depletion_mm", "weather_estimated"], order_by="date asc")
		self.assertTrue(all(r.weather_estimated for r in rows))
		self.assertAlmostEqual(rows[0].epan_mm, 4.0)
		self.assertGreater(rows[-1].depletion_mm, 0)

	def test_a_long_gap_is_estimated_from_the_last_readings_on_record(self):
		late = self.start + datetime.timedelta(days=60)
		BA.rebuild(farm=FARM, start=late, end=late + datetime.timedelta(days=2))
		r = frappe.get_all("Irrigation Block Balance", filters={"block": self.block, "date": str(late)}, fields=["epan_mm"])[0]
		self.assertAlmostEqual(r.epan_mm, 5.0, places=2)  # the last logged days, not 0

	def test_a_blank_1ft_irrometer_is_not_a_saturated_reading(self):
		wr = frappe.get_all("Weather Reading", filters={"farm": FARM, "date": "2026-07-10"}, pluck="name")[0]
		frappe.get_doc({"doctype": "Irrometer Reading", "parent": wr, "parenttype": "Weather Reading", "parentfield": "irrometer_reading",
		                "irrigation_block": self.block, "date": "2026-07-10", "irrometer_2ft_reading": 40}).db_insert()
		self.rebuild()
		self.assertFalse(self.rows()["2026-07-10"].irrometer_adjusted)

	def test_state_changing_endpoints_are_post_only(self):
		from upande_irrigation.api import runsheet

		for fn in (BA.rebuild, runsheet.generate, runsheet.set_status):
			self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[fn], ["POST"], fn.__name__)

	def test_missing_weather_is_estimated_and_flagged(self):
		BA.rebuild(farm=FARM, start=self.start, end=self.start + datetime.timedelta(days=14))
		r = self.rows()["2026-07-14"]
		self.assertTrue(r.weather_estimated)
		self.assertAlmostEqual(r.epan_mm, 5.0, places=2)

	def test_state_projects_the_trigger(self):
		self.rebuild()
		st = BA.state(FARM, as_of="2026-07-11")[self.block]
		self.assertAlmostEqual(st["mean_etc_mm"], 2.39, places=1)
		# 23.9 mm today, +2.39/day, RAW 48 mm: due in 10 days — beyond the 7-day window.
		self.assertIsNone(st["trigger_day"])
		self.assertAlmostEqual(st["projection"][-1], 23.9 + 7 * 2.39, places=0)
