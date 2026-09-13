"""Drop Block Type and its two satellites.

Block Type modelled an irrigation shift, not a block — its child rows already
named Warehouses, all 89 of which exist on the v16 destination. The grouping now
lives on Irrigation Scheduler.shift_blocks, so the trio is dead weight, and two of
the three belong to modules the destination does not have.

post_model_sync: Irrigation Planner.block must already be Data, which the code
DocType does during the model sync in the same migrate.
"""

import frappe

RETIRED = ("Blocks List", "Block Type", "Block configaration")


def execute():
	fieldtype = frappe.db.get_value(
		"DocField", {"parent": "Irrigation Planner", "fieldname": "block"}, "fieldtype"
	)
	if fieldtype != "Data":
		frappe.throw(
			f"Irrigation Planner.block is {fieldtype}, expected Data. "
			"Refusing to drop Block Type while planners still link to it."
		)

	for dt in RETIRED:
		if frappe.db.exists("DocType", dt):
			frappe.delete_doc("DocType", dt, force=True, ignore_permissions=True)

	frappe.clear_cache()
