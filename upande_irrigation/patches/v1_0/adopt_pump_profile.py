"""Move Irrigation Pump Profile into this app.

It was created under Upande Kaitet, an app the v16 destination does not have,
while allocate.py, resources.py and irrigation_planner.py all query it. A missing
DocType raises rather than degrading, so the module moves with the callers.

pre_model_sync, so the reassignment lands before migrate reads the DocTypes and
syncs the code definition onto the existing table.
"""

import frappe

DOCTYPE = "Irrigation Pump Profile"


def execute():
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	frappe.db.set_value("DocType", DOCTYPE, "module", "Upande Irrigation", update_modified=False)
	frappe.db.set_value("DocType", DOCTYPE, "custom", 0, update_modified=False)
	frappe.clear_cache()
