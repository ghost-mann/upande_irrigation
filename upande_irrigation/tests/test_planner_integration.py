"""End-to-end: a planner over a week the farm actually measured.

This is the case the old engine never produced. Lokitela has seven consecutive
Weather Readings ending 2026-07-12, so a plan week opening 2026-07-13 has a
complete elapsed week behind it. Every assertion here failed under the old engine,
which aggregated 2026-07-13 → 2026-07-19 — a week with no readings at all.

Hand-checked arithmetic for that window (Σ et_crop 20.14 mm, Σ rain 7.66 mm):
    clean ET crop  = 20.14 × 0.65        = 13.091 mm
    week deficit   = 13.091 − 7.66       =  5.431 mm
    required hours = 5.431 ÷ 2.8 ÷ 0.70  =  2.771 hr
"""

import frappe
from frappe.tests.utils import FrappeTestCase

FARM = "Lokitela"
PLAN_FROM = "2026-07-13"
PLAN_TO = "2026-07-19"
MEASURED_FROM = "2026-07-06"
MEASURED_TO = "2026-07-12"


class TestPlannerOverAMeasuredWeek(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.block = frappe.db.get_value(
			"Block Type", {"farm": FARM, "is_active": 1, "name": ["like", "% - SHIFT %"]}, "name"
		)
		cls.readings = frappe.db.count(
			"Weather Reading", {"farm": FARM, "date": ["between", [MEASURED_FROM, MEASURED_TO]]}
		)

	def _planner(self):
		if not self.block:
			self.skipTest(f"no active shifts on {FARM}")
		if self.readings < 7:
			self.skipTest(f"{FARM} has {self.readings} readings in the measured week, need 7")
		doc = frappe.new_doc("Irrigation Planner")
		doc.farm = FARM
		doc.block = self.block
		doc.from_date = PLAN_FROM
		doc.to_date = PLAN_TO
		doc.insert(ignore_permissions=True)
		return doc

	def test_it_measures_the_preceding_week(self):
		doc = self._planner()
		self.assertEqual(str(doc.measured_from), MEASURED_FROM)
		self.assertEqual(str(doc.measured_to), MEASURED_TO)
		self.assertIn("7 of 7", doc.weather_completeness)

	def test_it_finds_real_demand_where_the_old_engine_found_none(self):
		doc = self._planner()
		self.assertAlmostEqual(doc.clean_et_crop_mm, 13.091, places=2)
		self.assertAlmostEqual(doc.this_week_deficit, 5.431, places=2)
		self.assertGreater(doc.required_hours, 0.0)
		self.assertAlmostEqual(doc.required_hours, 2.771, places=2)
		self.assertEqual(doc.no_irrigation_reason, "")

	def test_the_hook_leaves_allocation_untouched(self):
		doc = self._planner()
		self.assertFalse(doc.shift_hours)
		self.assertFalse(doc.cycles_count)
		self.assertFalse(doc.scheduled_start)

	def test_resaving_does_not_change_demand(self):
		doc = self._planner()
		before = doc.required_hours
		doc.save(ignore_permissions=True)
		self.assertAlmostEqual(doc.required_hours, before, places=6)

	def test_resaving_does_not_wipe_an_allocation(self):
		# The old hook recomputed shift_hours on every save, overwriting whatever the
		# scheduler had granted.
		doc = self._planner()
		frappe.db.set_value("Irrigation Planner", doc.name, "shift_hours", 1.25, update_modified=False)
		doc.reload()
		doc.save(ignore_permissions=True)
		self.assertAlmostEqual(doc.shift_hours, 1.25, places=4)
