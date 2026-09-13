"""Tests for shift lookup after Block Type is retired.

The bug being locked out: Block Type rows were ordered by CAST(SUBSTRING_INDEX(...))
so SHIFT 10 sorted after SHIFT 9, not between SHIFT 1 and SHIFT 2. A naive string
sort over the new table silently reorders every section's irrigation.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation import shifts
from upande_irrigation.tests import ROW_META_KEYS

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
		# Meta keys (name included) must be stripped before a snapshot like this is
		# ever re-appended in tearDownClass. Otherwise Frappe treats each row's
		# leftover `name` as an existing row to update, but the clear+commit below
		# already deleted it, so tearDownClass's "restore" silently writes nothing
		# back. See TestShiftBlocksSnapshotRestore for the regression test.
		cls.saved = [
			{k: v for k, v in r.as_dict().items() if k not in ROW_META_KEYS}
			for r in (sched.shift_blocks or [])
		]
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


class TestShiftBlocksSnapshotRestore(FrappeTestCase):
	"""Regression for the bug in this file's own fixture (TestShiftLookup's
	setUpClass/tearDownClass above): snapshotting shift_blocks rows with plain
	row.as_dict() keeps `name`. setUpClass swaps in a throwaway fixture and commits —
	really deleting the original rows — then tearDownClass restores the snapshot and
	commits again. Because each restored row still carries its old `name`, Frappe
	treats it as an update to a row that commit already deleted, so the "restore"
	silently writes nothing back and the table nets to zero.

	This only reproduces with the same two-commit shape the real fixture uses: a
	committed swap, then a committed restore from a *separate* frappe.get_single()
	instance. A single uncommitted clear-and-reappend on one instance does not
	reproduce it — Frappe reconciles that correctly since nothing was actually
	deleted yet.

	The `finally` block re-restores for real (with ROW_META_KEYS stripped, which is
	the actual fix) if the count comes out wrong, so a failure here still leaves the
	site's shift_blocks table intact rather than wiped.
	"""

	def test_a_committed_swap_and_restore_preserves_the_row_count(self):
		sched = frappe.get_single("Irrigation Scheduler")
		before = len(sched.shift_blocks)
		self.assertGreater(before, 0, "need at least one real shift_blocks row to prove this")

		# This is the fixed (stripped) form. The `finally` block below also relies on
		# it to guarantee real data survives this test regardless of outcome.
		snapshot = [
			{k: v for k, v in row.as_dict().items() if k not in ROW_META_KEYS}
			for row in sched.shift_blocks
		]
		wh = frappe.get_all("Warehouse", filters={"is_group": 0}, pluck="name", limit=1)
		if not wh:
			self.skipTest("no leaf warehouse on this site to swap in")

		try:
			sched.set("shift_blocks", [])
			sched.append(
				"shift_blocks",
				{"shift": "TEST SWAP - SHIFT 1", "block": wh[0], "farm": "Lokitela", "is_active": 1},
			)
			sched.save(ignore_permissions=True)
			frappe.db.commit()

			restore = frappe.get_single("Irrigation Scheduler")
			restore.set("shift_blocks", [])
			for row in snapshot:
				restore.append("shift_blocks", row)
			restore.save(ignore_permissions=True)
			frappe.db.commit()

			after = len(frappe.get_single("Irrigation Scheduler").shift_blocks)
			self.assertEqual(after, before, "snapshot/restore cycle must not lose rows")
		finally:
			final = frappe.get_single("Irrigation Scheduler")
			if len(final.shift_blocks) != before:
				final.set("shift_blocks", [])
				for row in snapshot:
					final.append("shift_blocks", row)
				final.save(ignore_permissions=True)
				frappe.db.commit()
