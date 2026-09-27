"""Water and Electricity Meter Reading, adopted from the retired upande_kaitet.

On v15 the arithmetic lived in a Client Script, so a reading saved any other way
(import, API, the dashboard) stored whatever units_used it was given. It moves to
validate(), which insert_preserving suppresses — migrated rows keep their stored
units_used verbatim (67 of the 82 v15 water rows use a x10 meter factor that the
form's formula does not, and silently recomputing them would change history).
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.tests.helpers import NoCommit, make_warehouse

SECTION_A = "_Test Meter Section A"
SECTION_B = "_Test Meter Section B"


def _section(name):
	return make_warehouse(name, is_group=1)


class TestMeterReadings(NoCommit, FrappeTestCase):
	def setUp(self):
		frappe.db.delete("Electricity Meter Reading")
		frappe.db.delete("Water Meter Reading")

	def reading(self, doctype, **kw):
		return frappe.get_doc({"doctype": doctype, "date": "2026-09-01", **kw}).insert()

	def test_units_used_is_new_minus_previous(self):
		d = self.reading("Electricity Meter Reading", previous_reading=100, new_reading=130)
		self.assertEqual(d.units_used, 30)

	def test_a_reading_below_the_previous_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.reading("Electricity Meter Reading", previous_reading=100, new_reading=90)

	def test_previous_defaults_from_the_latest_reading(self):
		self.reading("Electricity Meter Reading", previous_reading=0, new_reading=500).submit()
		b = self.reading("Electricity Meter Reading", date="2026-09-02", new_reading=520)
		self.assertEqual(b.previous_reading, 500)
		self.assertEqual(b.units_used, 20)

	def test_water_previous_is_scoped_to_its_section(self):
		a, b = _section(SECTION_A), _section(SECTION_B)
		self.reading("Water Meter Reading", irrigation_section=a, previous_reading=0, new_reading=1000).submit()
		self.reading("Water Meter Reading", irrigation_section=b, previous_reading=0, new_reading=40).submit()
		nxt = self.reading("Water Meter Reading", irrigation_section=a, date="2026-09-02", new_reading=1010)
		self.assertEqual(nxt.previous_reading, 1000)
		self.assertEqual(nxt.units_used, 10)

	def test_a_back_dated_reading_takes_the_reading_before_it(self):
		"""An operator filling in a missed day: its baseline is the reading before
		that date, not the newest one (which would reject it as going backwards)."""
		self.reading("Electricity Meter Reading", date="2026-09-01", previous_reading=0, new_reading=12300).submit()
		self.reading("Electricity Meter Reading", date="2026-09-03", previous_reading=12400, new_reading=12500).submit()
		missed = self.reading("Electricity Meter Reading", date="2026-09-02", new_reading=12400)
		self.assertEqual(missed.previous_reading, 12300)
		self.assertEqual(missed.units_used, 100)

	def test_a_typed_previous_reading_is_kept(self):
		"""A new or replaced meter: the operator states its starting register."""
		self.reading("Electricity Meter Reading", previous_reading=0, new_reading=90000).submit()
		fresh = self.reading("Electricity Meter Reading", date="2026-09-02", previous_reading=15, new_reading=40)
		self.assertEqual(fresh.previous_reading, 15)
		self.assertEqual(fresh.units_used, 25)

	def test_cancelled_readings_are_not_a_baseline(self):
		first = self.reading("Electricity Meter Reading", previous_reading=0, new_reading=500)
		first.submit()
		first.cancel()
		nxt = self.reading("Electricity Meter Reading", date="2026-09-02", new_reading=520)
		self.assertEqual(nxt.previous_reading, 0)

	def test_migration_insert_keeps_stored_units(self):
		from upande_irrigation.migration import pull

		rec = {
			"name": "WMR-2026-01-99999", "creation": "2026-01-06 10:00:00", "modified": "2026-01-06 10:00:00",
			"owner": "Administrator", "modified_by": "Administrator", "docstatus": 1,
			"date": "2026-01-06 00:00:00", "irrigation_section": _section(SECTION_A),
			"previous_reading": 31633.0, "new_reading": 31647.0, "units_used": 140.0,
		}
		pull.insert_preserving("Water Meter Reading", [rec])
		self.assertEqual(frappe.db.get_value("Water Meter Reading", rec["name"], "units_used"), 140.0)
