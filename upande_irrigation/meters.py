"""Shared arithmetic for Water and Electricity Meter Reading.

Ported from the v15 "… Meter Reading Updater" Client Scripts, which did this in
the browser only. Here it runs in validate(), so a reading saved from the API or
the dashboard gets the same treatment as one typed into the form.

validate() is suppressed by migration.pull.insert_preserving, so migrated rows
keep the units_used they were stored with — see tests/test_meter_readings.py
for why that matters for the v15 water rows.
"""

import frappe
from frappe import _
from frappe.utils import flt


def apply_meter_rules(doc):
	if doc.previous_reading in (None, "") or (doc.is_new() and not flt(doc.previous_reading)):
		doc.previous_reading = latest_reading(doc)
	if doc.new_reading in (None, ""):
		return
	if flt(doc.new_reading) < flt(doc.previous_reading):
		frappe.throw(
			_("New Reading ({0}) cannot be less than the Previous Reading ({1}).").format(
				flt(doc.new_reading), flt(doc.previous_reading)
			),
			title=_("Meter went backwards"),
		)
	doc.units_used = flt(doc.new_reading) - flt(doc.previous_reading)


def latest_reading(doc):
	"""The New Reading of the latest non-cancelled reading on the same meter
	dated on or before this one.

	"On or before" is what lets an operator back-fill a missed day: its baseline
	is the day before, not the newest reading (which would reject it as the
	meter going backwards). A meter is identified by its irrigation_section;
	readings without one (all 310 v15 electricity rows) share a single meter."""
	filters = {"docstatus": ["<", 2], "name": ["!=", doc.name or ""]}
	if doc.date:
		filters["date"] = ["<=", doc.date]
	filters["irrigation_section"] = doc.irrigation_section or ["is", "not set"]
	rows = frappe.get_all(
		doc.doctype, filters=filters, fields=["new_reading"], order_by="date desc, creation desc", limit=1
	)
	return flt(rows[0].new_reading) if rows else 0
