"""Give pump capacity and per-shift rates real defaults.

Two gaps the first pass left open:

  · With no Irrigation Pump Profile the engine fell straight back to a full 168 hr
    week — technically honest but useless as a limit. Irrigation Settings now carries
    a farm-wide water target and pump flow rate, so a section without its own profile
    is capped against how this estate actually runs its pumps. The same figures
    become the DocField defaults on Irrigation Pump Profile, so a newly created
    profile starts from something sensible instead of two zeros.

  · Every shift computed identical hours because rate and coverage were farm-wide
    only. Block Type now carries both, blank meaning inherit. Depth in millimetres is
    per unit area, so a larger shift does not need a longer run — emitter output and
    wetted fraction are what actually change a shift's hours, and those are what can
    now be set per shift.

applied_rate_mm_hr and applied_coverage_pct record which two figures were in force
when a planner was computed, so the number stays auditable after a default changes.
"""

import frappe

PLANNER = "Irrigation Planner"
SETTINGS = "Irrigation Settings"
BLOCK_TYPE = "Block Type"
PUMP = "Irrigation Pump Profile"
MODULE = "Upande Irrigation"

# 20 h/day × 7 days at 25 m³/hr = 3500 m³/wk, leaving 4 h/day for maintenance and
# recovery. A defensible starting assumption for a micro-irrigation estate, and the
# figure every affected planner names in its warning so nobody mistakes it for a
# measurement.
DEFAULT_FLOW_M3_HR = 25.0
DEFAULT_TARGET_M3_WEEK = 3500.0

SETTINGS_FIELDS = [
	{
		"fieldname": "sec_pump_defaults",
		"label": "Pump Defaults",
		"fieldtype": "Section Break",
		"insert_after": "cycle_rest_hours",
	},
	{
		"fieldname": "default_pump_flow_rate_m3_per_hr",
		"label": "Default Pump Flow Rate (m³/hr)",
		"fieldtype": "Float",
		"insert_after": "sec_pump_defaults",
		"default": str(DEFAULT_FLOW_M3_HR),
		"precision": "2",
		"description": "Used when a section has no Irrigation Pump Profile.",
	},
	{
		"fieldname": "col_pump_defaults",
		"fieldtype": "Column Break",
		"insert_after": "default_pump_flow_rate_m3_per_hr",
	},
	{
		"fieldname": "default_water_target_m3_per_week",
		"label": "Default Water Target (m³/week)",
		"fieldtype": "Float",
		"insert_after": "col_pump_defaults",
		"default": str(DEFAULT_TARGET_M3_WEEK),
		"precision": "2",
		"description": (
			"Weekly volume a pump may deliver. Divided by the flow rate this gives the "
			"weekly hour budget — 3500 ÷ 25 = 140 hr/wk, i.e. 20 h/day."
		),
	},
]

BLOCK_TYPE_FIELDS = [
	{
		"fieldname": "sec_shift_hydraulics",
		"label": "Hydraulics",
		"fieldtype": "Section Break",
		"insert_after": "is_active",
	},
	{
		"fieldname": "application_rate_mm_hr",
		"label": "Application Rate (mm/hr)",
		"fieldtype": "Float",
		"insert_after": "sec_shift_hydraulics",
		"precision": "2",
		"description": (
			"Emitter output for this shift. Leave blank or 0 to inherit the farm default "
			"from Irrigation Settings."
		),
	},
	{
		"fieldname": "col_shift_hydraulics",
		"fieldtype": "Column Break",
		"insert_after": "application_rate_mm_hr",
	},
	{
		"fieldname": "irrigation_coverage",
		"label": "Irrigation Coverage (%)",
		"fieldtype": "Float",
		"insert_after": "col_shift_hydraulics",
		"precision": "1",
		"description": (
			"Fraction of the ground the emitters wet. Leave blank or 0 to inherit the "
			"farm default."
		),
	},
]

PLANNER_FIELDS = [
	{
		"fieldname": "applied_rate_mm_hr",
		"label": "Applied Rate (mm/hr)",
		"fieldtype": "Float",
		"insert_after": "measured_to",
		"read_only": 1,
		"precision": "2",
		"description": "The rate this planner's hours were computed with.",
	},
	{
		"fieldname": "applied_coverage_pct",
		"label": "Applied Coverage (%)",
		"fieldtype": "Float",
		"insert_after": "applied_rate_mm_hr",
		"read_only": 1,
		"precision": "1",
		"description": "The coverage this planner's hours were computed with.",
	},
]

# A new Irrigation Pump Profile opens with the farm-wide assumption already filled
# in, so creating one is a matter of correcting two numbers rather than sourcing them.
PUMP_DEFAULTS = {
	"pump_flow_rate_m3_per_hr": str(DEFAULT_FLOW_M3_HR),
	"water_target_m3_per_week": str(DEFAULT_TARGET_M3_WEEK),
}


def execute():
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	specs = {
		SETTINGS: SETTINGS_FIELDS,
		BLOCK_TYPE: BLOCK_TYPE_FIELDS,
		PLANNER: PLANNER_FIELDS,
	}
	for dt, fields in specs.items():
		if not frappe.db.exists("DocType", dt):
			continue
		missing = [
			f
			for f in fields
			if not frappe.db.exists("Custom Field", {"dt": dt, "fieldname": f["fieldname"]})
		]
		if missing:
			create_custom_fields({dt: missing}, ignore_validate=True)

	if frappe.db.exists("DocType", PUMP):
		for fieldname, value in PUMP_DEFAULTS.items():
			frappe.make_property_setter(
				{
					"doctype": PUMP,
					"fieldname": fieldname,
					"property": "default",
					"value": value,
					"property_type": "Text",
				},
				is_system_generated=False,
			)

	frappe.db.sql(
		"""
		UPDATE `tabCustom Field` SET module = %(module)s
		WHERE dt IN (%(settings)s, %(block_type)s, %(planner)s)
		  AND (module IS NULL OR module = '')
		""",
		{"module": MODULE, "settings": SETTINGS, "block_type": BLOCK_TYPE, "planner": PLANNER},
	)
	frappe.db.sql(
		"UPDATE `tabProperty Setter` SET module = %s WHERE doc_type = %s AND (module IS NULL OR module = '')",
		(MODULE, PUMP),
	)

	frappe.db.commit()
	frappe.clear_cache()
