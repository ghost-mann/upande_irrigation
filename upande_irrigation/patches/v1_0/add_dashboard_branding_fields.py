"""Move the dashboard's organisation label into Irrigation Settings.

The header read its organisation name from Global Defaults' default_company,
which is wrong twice over: it bakes one customer's company into a product meant
to be deployed at several farms, and it ties a dashboard label to an accounting
setting that exists for a different reason.

The label now lives on Irrigation Settings — a Single, one per site — so each
deployment names itself. The product name in the header is fixed ("Upande
Irrigation"); this is the line underneath it.
"""

import frappe

DOCTYPE = "Irrigation Settings"
MODULE = "Upande Irrigation"

FIELDS = [
    {
        "fieldname": "sec_dashboard",
        "label": "Dashboard",
        "fieldtype": "Section Break",
        "insert_after": "review_notes",
    },
    {
        "fieldname": "organisation_name",
        "label": "Organisation Name",
        "fieldtype": "Data",
        "insert_after": "sec_dashboard",
        "description": (
            "Shown beneath the product name in the /upande-irrigation header — the "
            "estate, group or company this deployment serves. Leave blank to show "
            "nothing."
        ),
    },
]


def execute():
    if not frappe.db.exists("DocType", DOCTYPE):
        return

    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    missing = [
        f
        for f in FIELDS
        if not frappe.db.exists("Custom Field", {"dt": DOCTYPE, "fieldname": f["fieldname"]})
    ]
    if missing:
        create_custom_fields({DOCTYPE: missing}, ignore_validate=True)

    # Stamp the module so these export with the app's fixtures, the same reason
    # add_planner_cycle_fields does it.
    frappe.db.sql(
        """
        UPDATE `tabCustom Field`
        SET module = %(module)s
        WHERE dt = %(dt)s AND (module IS NULL OR module = '')
        """,
        {"module": MODULE, "dt": DOCTYPE},
    )

    # Seed the existing site from the company it was previously reading, so the
    # header does not go blank on upgrade. New sites start empty and set it
    # themselves.
    settings = frappe.get_single(DOCTYPE)
    if not settings.get("organisation_name"):
        company = frappe.db.get_single_value("Global Defaults", "default_company")
        if company:
            frappe.db.set_value(DOCTYPE, DOCTYPE, "organisation_name", company)

    frappe.db.commit()
    frappe.clear_cache(doctype=DOCTYPE)
