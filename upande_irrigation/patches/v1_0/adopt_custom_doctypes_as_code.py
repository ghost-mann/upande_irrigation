"""Hand the nine fixture DocTypes over to the code definitions.

They were created through the UI as custom: 1, so their schema lived in the
database and bench migrate ignored them. The code DocTypes generated alongside
this patch carry the same fields — the DocType's own plus the Custom Fields six
patches added — so flipping custom to 0 lets migrate sync the code definition onto
the table that is already there. The table and its rows are untouched; only who
owns the definition changes.

The Custom Field rows go too. They are now duplicated inside the code DocType, and
leaving both would have migrate re-adding columns that already exist.

pre_model_sync, because it has to happen before migrate reads the DocTypes.
"""

import frappe

MANAGED = (
	"Irrigation Planner",
	"Irrigation Scheduler",
	"Irrigation Scheduler Run",
	"Irrigation Scheduler Run Shift",
	"Irrigation Settings",
	"Irrometer Reading",
	"Reservoir Pumping Record",
	"Water Transfer",
	"Weather Reading",
)


def execute():
	for dt in MANAGED:
		if not frappe.db.exists("DocType", dt):
			continue
		if frappe.db.get_value("DocType", dt, "custom"):
			frappe.db.set_value("DocType", dt, "custom", 0, update_modified=False)

	stale = frappe.get_all("Custom Field", filters={"dt": ["in", list(MANAGED)]}, pluck="name")
	for name in stale:
		frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True)

	frappe.clear_cache()
