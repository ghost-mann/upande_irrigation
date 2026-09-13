"""Tests for the effective-schema merge.

The bug being locked out: the app's DocType fixture carries 16 fields on
Irrigation Planner while the running site has 39 — the other 23 are Custom Field
rows created by patches. Converting from doctype.json alone would ship a DocType
missing required_hours, applied_rate_mm_hr and the measured-window fields, and the
engine writes all of them.
"""

from frappe.tests.utils import FrappeTestCase

from upande_irrigation.migration.schema import merge_custom_fields


DT = {
	"name": "Irrigation Planner",
	"custom": 1,
	"fields": [
		{"fieldname": "block", "fieldtype": "Link", "options": "Block Type"},
		{"fieldname": "to_date", "fieldtype": "Date"},
	],
}

CF = [
	{"dt": "Irrigation Planner", "fieldname": "required_hours",
	 "fieldtype": "Float", "insert_after": "to_date"},
	{"dt": "Irrigation Planner", "fieldname": "measured_from",
	 "fieldtype": "Date", "insert_after": "required_hours"},
]


class TestMergeCustomFields(FrappeTestCase):
	def test_custom_fields_land_after_their_anchor(self):
		out = merge_custom_fields(DT, CF)
		names = [f["fieldname"] for f in out["fields"]]
		self.assertEqual(names, ["block", "to_date", "required_hours", "measured_from"])

	def test_merge_clears_the_custom_flag(self):
		self.assertEqual(merge_custom_fields(DT, CF)["custom"], 0)

	def test_the_input_doctype_is_not_mutated(self):
		merge_custom_fields(DT, CF)
		self.assertEqual(len(DT["fields"]), 2)

	def test_an_unknown_anchor_appends_at_the_end(self):
		cf = [{"dt": "Irrigation Planner", "fieldname": "orphan",
		       "fieldtype": "Data", "insert_after": "does_not_exist"}]
		names = [f["fieldname"] for f in merge_custom_fields(DT, cf)["fields"]]
		self.assertEqual(names[-1], "orphan")
