"""Tests for record insertion that keeps identity.

The bug being locked out: frappe.get_doc().insert() mints a fresh name and stamps
creation with now(). Irrigation Planner names are referenced in Irrigation
Scheduler Run Shift.planner, and every dashboard chart is drawn over creation
dates, so both have to survive the move.

A second bug lives here too: doc.insert() also runs validate/before_save, and
Weather Reading and Irrigation Planner both carry hooks (compute_derived,
compute_shift) that recompute derived agronomy fields from this site's current
Irrigation Settings. Running those during a data import silently rewrites the
values being imported instead of carrying them across verbatim -- discovered
when the real load overwrote et_pan/swd on real records. insert_preserving must
suppress validate/before_save (ignore_validate) so imported values land as-is.

Note: the fixture date is 2030-01-01, deliberately outside the real Lokitela
Weather Reading history (2010-01-01..2026-07-24) now loaded on this site, so
these tests do not collide with production rows the same way TEST-WX-0001
would if it fell inside that range.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.migration.pull import insert_preserving

RECORD = {
	"name": "TEST-WX-0001",
	"creation": "2030-01-01 03:04:05.000000",
	"owner": "Administrator",
	"farm": "Lokitela",
	"date": "2030-01-01",
}

# Deliberately internally-inconsistent derived values: if compute_derived ran,
# pan_depth_mm would be recomputed as pan_cups * depth_per_cup (2.5 with the
# default 0.5 mm/cup), not 999.0. Any of these fields changing after insert
# means the before_save hook fired.
HOOK_RECORD = {
	"name": "TEST-WX-0002",
	"creation": "2030-01-02 03:04:05.000000",
	"owner": "Administrator",
	"farm": "Lokitela",
	"date": "2030-01-02",
	"rainfall_mm": 10.0,
	"pan_cups": 5.0,
	"pan_depth_mm": 999.0,
	"minimum_temperature": 10.0,
	"maximum_temperature": 20.0,
	"et_pan": 888.0,
	"et_crop": 777.0,
	"cumulative_temperature": 666.0,
	"swd": -555.0,
	"z_value": 444.0,
}


class TestInsertPreserving(FrappeTestCase):
	def tearDown(self):
		frappe.db.delete("Weather Reading", {"name": RECORD["name"]})
		frappe.db.commit()

	def test_it_keeps_the_source_name(self):
		insert_preserving("Weather Reading", [RECORD])
		self.assertTrue(frappe.db.exists("Weather Reading", RECORD["name"]))

	def test_it_keeps_the_source_creation(self):
		insert_preserving("Weather Reading", [RECORD])
		creation = str(frappe.db.get_value("Weather Reading", RECORD["name"], "creation"))
		self.assertTrue(creation.startswith("2030-01-01 03:04:05"))

	def test_an_existing_record_is_skipped_not_duplicated(self):
		insert_preserving("Weather Reading", [RECORD])
		self.assertEqual(insert_preserving("Weather Reading", [RECORD]), 0)


class TestInsertPreservingSkipsHooks(FrappeTestCase):
	def tearDown(self):
		frappe.db.delete("Weather Reading", {"name": HOOK_RECORD["name"]})
		frappe.db.commit()

	def test_before_save_hook_does_not_recompute_derived_fields(self):
		insert_preserving("Weather Reading", [HOOK_RECORD])
		stored = frappe.db.get_value(
			"Weather Reading",
			HOOK_RECORD["name"],
			["pan_depth_mm", "et_pan", "et_crop", "cumulative_temperature", "swd", "z_value"],
			as_dict=True,
		)
		self.assertEqual(float(stored.pan_depth_mm), 999.0)
		self.assertEqual(float(stored.et_pan), 888.0)
		self.assertEqual(float(stored.et_crop), 777.0)
		self.assertEqual(float(stored.cumulative_temperature), 666.0)
		self.assertEqual(float(stored.swd), -555.0)
		self.assertEqual(float(stored.z_value), 444.0)
