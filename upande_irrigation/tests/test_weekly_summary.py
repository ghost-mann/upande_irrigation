"""The weekly water balance per section, as the Budget table and the report read it.

Locks out: a big block and a small one averaged as equals; Σ over block-days
mistaken for the section's mm; end-of-week depletion taken from the wrong day;
hours applied counting Planned cycles; zero-area blocks dividing by zero.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.api import weekly as W
from upande_irrigation.tests.helpers import make_farm, make_warehouse

FARM = "_Test Weekly Farm"
MONDAY = "2026-07-06"


class TestWeeklySummary(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_farm(FARM)
		cls.section = make_warehouse("_Test WK Section", is_group=1, farm=FARM)
		cls.a = make_warehouse("_Test WK Block A", parent=cls.section, farm=FARM)
		cls.b = make_warehouse("_Test WK Block B", parent=cls.section, farm=FARM)

	def setUp(self):
		frappe.db.delete("Irrigation Block Balance", {"farm": FARM})
		for name in frappe.get_all("Irrigation Run Sheet", filters={"farm": FARM}, pluck="name"):
			frappe.delete_doc("Irrigation Run Sheet", name, force=True, ignore_permissions=True)
		for blk in (self.a, self.b):
			frappe.db.delete("Irrigation Block Profile", {"block": blk})

	def seed(self, area_a, area_b):
		for blk, area, etc, dep in ((self.a, area_a, 2, 10), (self.b, area_b, 4, 20)):
			frappe.get_doc({"doctype": "Irrigation Block Profile", "block": blk, "area_ha": area, "tree_count": 0}).insert(ignore_permissions=True)
			for k in range(7):
				day = frappe.utils.add_days(MONDAY, k)
				frappe.get_doc({"doctype": "Irrigation Block Balance", "block": blk, "farm": FARM, "date": day,
				                "epan_mm": etc * 2, "etc_mm": etc, "rain_mm": 1, "effective_rain_mm": 0.5,
				                "irrigation_mm": 1 if k == 3 else 0, "depletion_mm": dep if k == 6 else 99}).db_insert()
		frappe.get_doc({"doctype": "Irrigation Run Sheet", "farm": FARM, "date": frappe.utils.add_days(MONDAY, 2), "runs": [
			{"shift": "_WK - SHIFT 1", "section": self.section, "pump": "p", "planned_hours": 3, "actual_hours": 2, "status": "Done"},
			{"shift": "_WK - SHIFT 2", "section": self.section, "pump": "p", "planned_hours": 4, "status": "Planned"},
			{"shift": "_WK - SHIFT 3", "section": self.section, "pump": "p", "planned_hours": 5, "status": "Skipped", "skip_reason": "x"},
		]}).insert(ignore_permissions=True)

	def row(self, **kw):
		rows = W.summary(farm=FARM, from_date=MONDAY, to_date=frappe.utils.add_days(MONDAY, 6), **kw)["rows"]
		self.assertEqual(len(rows), 1)
		return rows[0]

	def test_area_weighted_section_figures(self):
		self.seed(1, 3)
		r = self.row()
		self.assertEqual(r["section"], self.section)
		self.assertEqual(r["week"], MONDAY)
		self.assertEqual(r["days"], 7)
		self.assertAlmostEqual(r["etc_mm"], (14 * 1 + 28 * 3) / 4)  # 24.5
		self.assertAlmostEqual(r["epan_mm"], 49.0)
		self.assertAlmostEqual(r["rain_mm"], 7.0)
		self.assertAlmostEqual(r["effective_rain_mm"], 3.5)
		self.assertAlmostEqual(r["deficit_mm"], 21.0)
		self.assertAlmostEqual(r["irrigation_mm"], 1.0)
		self.assertAlmostEqual(r["end_depletion_mm"], (10 * 1 + 20 * 3) / 4)  # 17.5

	def test_hours_planned_and_applied(self):
		self.seed(1, 3)
		r = self.row()
		self.assertEqual(r["hours_planned"], 7.0)  # Done 3 + Planned 4; Skipped not counted
		self.assertEqual(r["hours_applied"], 2.0)  # Done at its actual hours

	def test_zero_area_falls_back_to_a_plain_mean(self):
		self.seed(0, 0)
		self.assertAlmostEqual(self.row()["etc_mm"], 21.0)

	def test_section_filter(self):
		self.seed(1, 3)
		self.assertEqual(W.summary(farm=FARM, section="nope", from_date=MONDAY, to_date=MONDAY)["rows"], [])

	def test_report_returns_the_same_rows(self):
		from upande_irrigation.upande_irrigation.report.weekly_irrigation_summary import weekly_irrigation_summary as rpt

		self.seed(1, 3)
		columns, data = rpt.execute({"farm": FARM, "from_date": MONDAY, "to_date": frappe.utils.add_days(MONDAY, 6)})
		self.assertIn("etc_mm", [c["fieldname"] for c in columns])
		self.assertAlmostEqual(data[0]["etc_mm"], 24.5)
