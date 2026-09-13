"""Tests for shift lookup after Block Type is retired.

The bug being locked out: Block Type rows were ordered by CAST(SUBSTRING_INDEX(...))
so SHIFT 10 sorted after SHIFT 9, not between SHIFT 1 and SHIFT 2. A naive string
sort over the new table silently reorders every section's irrigation.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation import shifts

FARM = "Lokitela"
ROWS = [
	{"shift": "ZZ - SHIFT 1", "block": None, "farm": FARM, "is_active": 1,
	 "application_rate_mm_hr": 0, "irrigation_coverage": 0},
	{"shift": "ZZ - SHIFT 2", "block": None, "farm": FARM, "is_active": 1,
	 "application_rate_mm_hr": 4.5, "irrigation_coverage": 70.0},
	{"shift": "ZZ - SHIFT 10", "block": None, "farm": FARM, "is_active": 1,
	 "application_rate_mm_hr": 0, "irrigation_coverage": 0},
	{"shift": "ZZ - SHIFT 3", "block": None, "farm": FARM, "is_active": 0,
	 "application_rate_mm_hr": 0, "irrigation_coverage": 0},
	{"shift": "YY - SHIFT 1", "block": None, "farm": FARM, "is_active": 1,
	 "application_rate_mm_hr": 0, "irrigation_coverage": 0},
	{"shift": "YY - SHIFT 01", "block": None, "farm": FARM, "is_active": 1,
	 "application_rate_mm_hr": 0, "irrigation_coverage": 0},
]


class TestShiftLookup(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		wh = frappe.get_all(
			"Warehouse", filters={"is_group": 0}, pluck="name", limit=1
		)
		cls.wh = wh[0] if wh else None
		sched = frappe.get_single("Irrigation Scheduler")
		cls.saved = [r.as_dict() for r in (sched.shift_blocks or [])]
		sched.set("shift_blocks", [])
		for r in ROWS:
			sched.append("shift_blocks", {**r, "block": cls.wh})
		sched.save(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		sched = frappe.get_single("Irrigation Scheduler")
		sched.set("shift_blocks", [])
		for r in cls.saved:
			sched.append("shift_blocks", r)
		sched.save(ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def test_shifts_sort_numerically_not_lexically(self):
		self.assertEqual(
			shifts.active_shifts(FARM, "ZZ"), ["ZZ - SHIFT 1", "ZZ - SHIFT 2", "ZZ - SHIFT 10"]
		)

	def test_inactive_shifts_are_excluded(self):
		self.assertNotIn("ZZ - SHIFT 3", shifts.active_shifts(FARM, "ZZ"))

	def test_count_matches_the_list(self):
		self.assertEqual(shifts.count_active_shifts(FARM, "ZZ"), 3)

	def test_overrides_come_back_for_a_shift_that_has_them(self):
		self.assertEqual(
			shifts.shift_overrides("ZZ - SHIFT 2"),
			{"application_rate_mm_hr": 4.5, "irrigation_coverage": 70.0},
		)

	def test_an_unknown_shift_gives_zeros_not_an_error(self):
		self.assertEqual(
			shifts.shift_overrides("NOPE - SHIFT 1"),
			{"application_rate_mm_hr": 0.0, "irrigation_coverage": 0.0},
		)

	def test_shifts_tying_on_shift_number_sort_deterministically(self):
		"""Bug locked out: names were deduped through a set before sorting, so two

		shifts tying on _shift_number (e.g. a stray leading zero, "SHIFT 01" next to
		"SHIFT 1") came back in whatever order that process's PYTHONHASHSEED gave the
		set iteration — a different, silently wrong order on every run, not just a
		wrong one.
		"""
		self.assertEqual(
			shifts.active_shifts(FARM, "YY"), ["YY - SHIFT 01", "YY - SHIFT 1"]
		)
