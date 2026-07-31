"""Create the cycle fields on Irrigation Planner and claim the orphan ones.

Two problems this fixes.

1. The back-to-back cycle split (events/irrigation_planner.py) writes
   `cycles_count`, `cycle_hours_each` and `cycle_plan`, and
   api.scheduler.live_sections SELECTs the first two — but the fields were never
   created. Setting an unknown attribute on a Document is silently dropped at
   save time, so compute_shift looked fine while live_sections failed outright
   with "Unknown column 'p.cycles_count'", taking the live shift view with it.

   The table does carry two columns from an earlier iteration of this feature,
   `number_of_cycles` and `hrscycle`, with no DocField or Custom Field behind
   them. They are left in place: dropping columns is not reversible, and nothing
   reads them. They simply stay orphaned.

2. Every Custom Field on Irrigation Planner had `module = NULL`, so the
   `[["module","=","Upande Irrigation"]]` fixture filter in hooks.py matched
   nothing and fixtures/custom_field.json exported empty. A fresh install got a
   planner missing `farm`, `shift_hours`, `scheduled_start` and the rest — every
   field compute_shift writes. Stamping the module makes the export work.
"""

import frappe

DOCTYPE = "Irrigation Planner"
MODULE = "Upande Irrigation"

CYCLE_FIELDS = [
    {
        "fieldname": "cycles_count",
        "label": "Cycles",
        "fieldtype": "Int",
        "insert_after": "shift_hours",
        "read_only": 1,
        "description": "Number of equal back-to-back cycles this shift is split into.",
    },
    {
        "fieldname": "cycle_hours_each",
        "label": "Hours per Cycle",
        "fieldtype": "Float",
        "insert_after": "cycles_count",
        "read_only": 1,
        "precision": "2",
    },
    {
        "fieldname": "cycle_plan",
        "label": "Cycle Plan",
        "fieldtype": "Data",
        "insert_after": "cycle_hours_each",
        "read_only": 1,
        "description": 'Human-readable summary, e.g. "3 × 4.50 hr".',
    },
]


def execute():
    if not frappe.db.exists("DocType", DOCTYPE):
        return

    _create_cycle_fields()
    _claim_module()

    frappe.clear_cache(doctype=DOCTYPE)


def _create_cycle_fields():
    """Add the three cycle fields, skipping any that already exist."""
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    missing = [
        spec
        for spec in CYCLE_FIELDS
        if not frappe.db.exists("Custom Field", {"dt": DOCTYPE, "fieldname": spec["fieldname"]})
    ]
    if not missing:
        return

    create_custom_fields({DOCTYPE: missing}, ignore_validate=True)
    frappe.db.commit()


def _claim_module():
    """Stamp module on this doctype's Custom Fields so fixtures export them."""
    frappe.db.sql(
        """
        UPDATE `tabCustom Field`
        SET module = %(module)s
        WHERE dt = %(dt)s
          AND (module IS NULL OR module = '')
        """,
        {"module": MODULE, "dt": DOCTYPE},
    )
    frappe.db.commit()
