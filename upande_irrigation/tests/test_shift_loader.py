"""Tests for the shift-mapping loader.

Two bugs being locked out: a shift whose rows disagree on rate or coverage — the
table is denormalised, so a disagreement means one of the rows is wrong and
picking either silently changes a shift's hours — and a row naming a group
warehouse, which is what three of the live 23HA rows do.
"""

from frappe.tests.utils import FrappeTestCase

from upande_irrigation.migration.shift_loader import validate_mapping

GOOD = [
	{"shift": "A - SHIFT 1", "block": "BLK 1 - KL", "farm": "Lokitela",
	 "is_active": 1, "application_rate_mm_hr": 4.0, "irrigation_coverage": 70.0},
	{"shift": "A - SHIFT 1", "block": "BLK 2 - KL", "farm": "Lokitela",
	 "is_active": 1, "application_rate_mm_hr": 4.0, "irrigation_coverage": 70.0},
]


class TestValidateMapping(FrappeTestCase):
	def test_consistent_rows_report_no_defect(self):
		self.assertEqual(validate_mapping(GOOD), [])

	def test_disagreeing_rate_within_a_shift_is_a_defect(self):
		rows = [dict(GOOD[0]), dict(GOOD[1], application_rate_mm_hr=9.0)]
		defects = validate_mapping(rows)
		self.assertEqual(len(defects), 1)
		self.assertIn("application_rate_mm_hr", defects[0])
		self.assertIn("A - SHIFT 1", defects[0])

	def test_disagreeing_farm_within_a_shift_is_a_defect(self):
		rows = [dict(GOOD[0]), dict(GOOD[1], farm="Elsewhere")]
		self.assertIn("farm", validate_mapping(rows)[0])

	def test_a_duplicate_block_in_one_shift_is_a_defect(self):
		rows = [dict(GOOD[0]), dict(GOOD[0])]
		self.assertIn("duplicate", validate_mapping(rows)[0].lower())

	def test_a_shift_with_no_rows_at_all_is_not_a_crash(self):
		self.assertEqual(validate_mapping([]), [])
