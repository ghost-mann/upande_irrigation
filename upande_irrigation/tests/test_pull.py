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

# Carries every field Weather Reading marks reqd. insert_preserving now asserts
# mandatory presence per record before inserting anything (a blanket
# ignore_mandatory=True used to let a short export row load silently), so a
# fixture standing in for an export row has to look like one.
RECORD = {
	"name": "TEST-WX-0001",
	"creation": "2030-01-01 03:04:05.000000",
	"owner": "Administrator",
	"farm": "Lokitela",
	"date": "2030-01-01",
	"rainfall_mm": 0.0,
	"pan_cups": 1.0,
	"minimum_temperature": 10.0,
	"maximum_temperature": 20.0,
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


class TestInsertPreservingAssumptions(FrappeTestCase):
	"""
	insert_preserving suppresses only validate and before_save via ignore_validate.
	It does NOT suppress before_validate, before_insert, after_insert, on_update,
	or on_change. If any of the six migrated doctypes later gain hooks for those
	entry points, imported data will silently corrupt (as happened to et_pan when
	validate ran during migration). This test fails the build if that assumption
	stops holding, forcing explicit review of insert_preserving before adding such
	hooks.
	"""

	MIGRATED_DOCTYPES = (
		"Tank And Valve",
		"Weather Reading",
		"Irrigation Planner",
		"Reservoir Pumping Record",
		"Water Transfer",
		"Irrigation Scheduler Run",
	)

	UNSUPPRESSED_ENTRY_POINTS = (
		"before_validate",
		"before_insert",
		"after_insert",
		"on_update",
		"on_change",
	)

	def test_no_unsuppressed_hooks_on_migrated_doctypes(self):
		"""Fail if upande_irrigation registers unsuppressed hooks on migrated doctypes."""
		doc_events = frappe.get_hooks("doc_events") or {}
		failures = []

		for doctype in self.MIGRATED_DOCTYPES:
			hooks = doc_events.get(doctype, {})
			for entry_point in self.UNSUPPRESSED_ENTRY_POINTS:
				if entry_point in hooks:
					# Check only upande_irrigation hooks, not system or other app hooks
					ui_hooks = [h for h in hooks[entry_point] if "upande_irrigation" in h]
					if ui_hooks:
						failures.append(
							f"{doctype}: upande_irrigation registered '{entry_point}' hook. "
							f"insert_preserving suppresses only validate/before_save, so this hook "
							f"will run and silently corrupt imported data. Review insert_preserving and update it "
							f"before adding this hook."
						)

		self.assertEqual(
			failures,
			[],
			"\n".join(failures) if failures else None,
		)

	def test_no_unsuppressed_methods_on_migrated_doctype_controllers(self):
		"""Fail if any migrated doctype controller defines unsuppressed entry point methods."""
		failures = []

		for doctype in self.MIGRATED_DOCTYPES:
			try:
				controller_class = frappe.get_doc(doctype).get_controller()
			except (frappe.DoesNotExistError, ImportError):
				continue

			for entry_point in self.UNSUPPRESSED_ENTRY_POINTS:
				if hasattr(controller_class, entry_point) and callable(getattr(controller_class, entry_point)):
					method = getattr(controller_class, entry_point)
					# Skip inherited methods from Document base class
					if not method.__qualname__.startswith("Document."):
						failures.append(
							f"{doctype}: method '{entry_point}' defined in controller. "
							f"insert_preserving suppresses only validate/before_save, so this method "
							f"will run and silently corrupt imported data. Review insert_preserving and update it "
							f"before adding this method."
						)

		self.assertEqual(
			failures,
			[],
			"\n".join(failures) if failures else None,
		)


class TestShortExportRowsAreRefused(FrappeTestCase):
	"""The bug being locked out: ignore_mandatory=True was applied blanket to every
	record of every doctype, so an export row missing a field the DocType marks
	reqd would have loaded silently -- against production data. The check now runs
	over the whole batch before anything is inserted, so a short row stops the load
	at record zero rather than 5000 rows in.
	"""

	SHORT = {"name": "TEST-WX-0003", "farm": "Lokitela", "date": "2030-01-03"}

	def tearDown(self):
		frappe.db.delete("Weather Reading", {"name": self.SHORT["name"]})
		frappe.db.commit()

	def test_a_missing_required_field_raises_and_names_it(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			insert_preserving("Weather Reading", [self.SHORT])
		self.assertIn("rainfall_mm", str(caught.exception))

	def test_nothing_is_inserted_when_any_record_is_short(self):
		with self.assertRaises(frappe.ValidationError):
			insert_preserving("Weather Reading", [RECORD, self.SHORT])
		self.assertFalse(frappe.db.exists("Weather Reading", RECORD["name"]))

	def test_a_zero_is_a_value_not_a_missing_field(self):
		"""rainfall_mm is reqd and legitimately 0.0 on a dry day."""
		insert_preserving("Weather Reading", [RECORD])
		self.assertTrue(frappe.db.exists("Weather Reading", RECORD["name"]))
		frappe.db.delete("Weather Reading", {"name": RECORD["name"]})
		frappe.db.commit()
