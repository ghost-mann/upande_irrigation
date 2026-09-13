"""Tests for the shift-mapping loader.

Bugs locked out: a shift whose rows disagree on rate or coverage — the table is
denormalised, so a disagreement means one of the rows is wrong and picking either
silently changes a shift's hours — a row naming a group warehouse, which is what
three of the live 23HA rows do, reaching frappe.throw() partway through a save
instead of being reported and skipped, and describe_mapping's cross-shift
observations being mistaken for validate_mapping's within-shift defects (they must
never gate a strict load).
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.migration.shift_loader import (
	SCHEDULER,
	describe_mapping,
	load_mapping,
	validate_mapping,
)
from upande_irrigation.tests import ROW_META_KEYS

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


class TestDescribeMapping(FrappeTestCase):
	"""describe_mapping is advisory only: it must never influence validate_mapping's
	verdict, however much it has to say about the same rows.
	"""

	def test_identical_block_sets_across_shifts_are_reported(self):
		rows = [
			{"shift": "ZZTEST - SHIFT 1", "block": "ZZTEST BLK 1 - KL"},
			{"shift": "ZZTEST - SHIFT 2", "block": "ZZTEST BLK 1 - KL"},
		]
		notes = describe_mapping(rows)
		self.assertTrue(
			any("ZZTEST - SHIFT 1" in n and "ZZTEST - SHIFT 2" in n and "identical" in n for n in notes)
		)

	def test_a_block_used_in_more_than_one_shift_is_reported(self):
		rows = [
			{"shift": "ZZTEST - SHIFT 1", "block": "ZZTEST BLK 1 - KL"},
			{"shift": "ZZTEST - SHIFT 1", "block": "ZZTEST BLK 2 - KL"},
			{"shift": "ZZTEST - SHIFT 2", "block": "ZZTEST BLK 1 - KL"},
		]
		notes = describe_mapping(rows)
		self.assertTrue(any("ZZTEST BLK 1 - KL" in n and "more than one shift" in n for n in notes))

	def test_disjoint_single_block_shifts_report_neither(self):
		rows = [
			{"shift": "ZZTEST - SHIFT 1", "block": "ZZTEST BLK 1 - KL"},
			{"shift": "ZZTEST - SHIFT 2", "block": "ZZTEST BLK 2 - KL"},
		]
		notes = describe_mapping(rows)
		self.assertFalse(any("ZZTEST" in n for n in notes))

	def test_notes_never_gate_a_strict_load(self):
		# Same rows describe_mapping has plenty to say about, but validate_mapping's
		# scope is within-shift agreement only -- it must report zero defects here.
		rows = [
			{"shift": "ZZTEST - SHIFT 1", "block": "ZZTEST BLK 1 - KL", "farm": "Lokitela",
			 "is_active": 1, "application_rate_mm_hr": 0, "irrigation_coverage": 0},
			{"shift": "ZZTEST - SHIFT 2", "block": "ZZTEST BLK 1 - KL", "farm": "Lokitela",
			 "is_active": 1, "application_rate_mm_hr": 0, "irrigation_coverage": 0},
		]
		self.assertTrue(describe_mapping(rows), "expected this fixture to produce advisory notes")
		self.assertEqual(validate_mapping(rows), [])

	def test_an_empty_mapping_is_not_a_crash(self):
		self.assertIsInstance(describe_mapping([]), list)


class TestLoadMappingExcludesGroupWarehouses(FrappeTestCase):
	"""Bug locked out: a mapping row naming a group warehouse used to reach
	sched.save() unguarded, and the child DocType's validate() hook threw partway
	through the load instead of the row being named and skipped. load_mapping must
	pre-check and report it in "excluded" instead.

	This calls the real load_mapping against the real Irrigation Scheduler, so it
	snapshots the site's actual shift_blocks first (with ROW_META_KEYS stripped --
	see test_shifts.py's TestShiftBlocksSnapshotRestore for why that matters) and
	restores it in a `finally`, regardless of outcome.
	"""

	def test_a_group_warehouse_row_is_excluded_not_thrown(self):
		group_warehouses = frappe.get_all("Warehouse", filters={"is_group": 1}, pluck="name", limit=1)
		leaf_warehouses = frappe.get_all("Warehouse", filters={"is_group": 0}, pluck="name", limit=1)
		if not group_warehouses or not leaf_warehouses:
			self.skipTest("need at least one group and one leaf warehouse on this site")

		sched = frappe.get_single(SCHEDULER)
		before = len(sched.shift_blocks)
		snapshot = [
			{k: v for k, v in row.as_dict().items() if k not in ROW_META_KEYS}
			for row in sched.shift_blocks
		]

		rows = [
			{"shift": "ZZTEST - SHIFT 1", "block": group_warehouses[0], "farm": "Lokitela",
			 "is_active": 1, "application_rate_mm_hr": 0, "irrigation_coverage": 0},
			{"shift": "ZZTEST - SHIFT 2", "block": leaf_warehouses[0], "farm": "Lokitela",
			 "is_active": 1, "application_rate_mm_hr": 0, "irrigation_coverage": 0},
		]
		try:
			result = load_mapping(rows, strict=True)  # must not raise
			self.assertEqual(result["loaded"], 1)
			self.assertEqual(len(result["excluded"]), 1)
			self.assertEqual(result["excluded"][0]["shift"], "ZZTEST - SHIFT 1")
			self.assertEqual(result["excluded"][0]["block"], group_warehouses[0])
			self.assertIn("group warehouse", result["excluded"][0]["reason"])
			self.assertIsInstance(result["notes"], list)
		finally:
			load_mapping(snapshot, strict=True)
			self.assertEqual(len(frappe.get_single(SCHEDULER).shift_blocks), before)
