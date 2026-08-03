"""Fields the redesigned engine needs, and removal of the ones it does not.

required_hours separates demand from what the allocator granted. shift_hours used
to be both, so opening a scheduled planner and saving it recomputed an uncapped
figure over the scheduler's capped one. measured_from/measured_to record which
elapsed week the demand came from — the fact the old engine never had, and the
reason 819 of 836 planners silently reported no demand.

irrigation_calculations goes because its per-block mm_hr and coverage never
reached the calculation: hours always came from the farm defaults, so the columns
invited edits that changed nothing. Its rows were rebuilt on every save, so
dropping the child doctype loses no recorded data.
"""

import frappe

PLANNER = "Irrigation Planner"
SETTINGS = "Irrigation Settings"
MODULE = "Upande Irrigation"

PLANNER_FIELDS = [
	{
		"fieldname": "required_hours",
		"label": "Required Hours",
		"fieldtype": "Float",
		"insert_after": "total_water_needed_mm",
		"read_only": 1,
		"precision": "2",
		"description": "Hours needed to meet demand, before pump capping. Compare with Shift Hours.",
	},
	{
		"fieldname": "measured_from",
		"label": "Measured From",
		"fieldtype": "Date",
		"insert_after": "required_hours",
		"read_only": 1,
		"description": "Start of the elapsed week this demand was measured over.",
	},
	{
		"fieldname": "measured_to",
		"label": "Measured To",
		"fieldtype": "Date",
		"insert_after": "measured_from",
		"read_only": 1,
		"description": "End of that week — always the day before From Date.",
	},
]

SETTINGS_FIELDS = [
	{
		"fieldname": "min_weather_days",
		"label": "Minimum Weather Readings per Week",
		"fieldtype": "Int",
		"insert_after": "organisation_name",
		"default": "7",
		"description": (
			"Below this many daily readings in the measured week, no planner is created "
			"for that farm-week and the run records why. 7 refuses on a single missed day."
		),
	},
	{
		"fieldname": "cycle_rest_hours",
		"label": "Rest Between Cycles (Hrs)",
		"fieldtype": "Float",
		"insert_after": "min_weather_days",
		"default": "2",
		"precision": "1",
		"description": "Gap between a shift's cycles, so water can infiltrate before the next one.",
	},
]


def execute():
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	for dt, specs in ((PLANNER, PLANNER_FIELDS), (SETTINGS, SETTINGS_FIELDS)):
		if not frappe.db.exists("DocType", dt):
			continue
		missing = [
			f
			for f in specs
			if not frappe.db.exists("Custom Field", {"dt": dt, "fieldname": f["fieldname"]})
		]
		if missing:
			create_custom_fields({dt: missing}, ignore_validate=True)

	# shift_schedule_section anchors on the table we are about to drop, and
	# scheduled_start still anchors on irrigation_deficit_mm — a field that never
	# existed. Repoint both onto fields that do, or the form silently loses a whole
	# section when Frappe cannot resolve the anchor.
	frappe.db.set_value(
		"Custom Field",
		{"dt": PLANNER, "fieldname": "shift_schedule_section"},
		"insert_after",
		"no_irrigation_reason",
		update_modified=False,
	)
	frappe.db.set_value(
		"Custom Field",
		{"dt": PLANNER, "fieldname": "scheduled_start"},
		"insert_after",
		"shift_schedule_section",
		update_modified=False,
	)

	frappe.db.delete("Custom Field", {"dt": PLANNER, "fieldname": "irrigation_calculations"})
	if frappe.db.exists("DocType", "Irrigation Calculation"):
		frappe.delete_doc("DocType", "Irrigation Calculation", force=1, ignore_permissions=True)

	# Custom Fields created outside a fixtures workflow carry module = NULL, which
	# makes export-fixtures skip them entirely.
	frappe.db.sql(
		"""
		UPDATE `tabCustom Field` SET module = %(module)s
		WHERE dt IN (%(planner)s, %(settings)s) AND (module IS NULL OR module = '')
		""",
		{"module": MODULE, "planner": PLANNER, "settings": SETTINGS},
	)

	frappe.db.commit()
	frappe.clear_cache()
