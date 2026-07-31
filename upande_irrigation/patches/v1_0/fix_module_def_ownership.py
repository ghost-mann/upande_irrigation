"""Stop treating the app's own module as a custom module.

The `Upande Irrigation` Module Def carried `custom = 1` while also naming
`app_name = upande_irrigation`, and modules.txt does list the module. Frappe
routes custom modules through `get_custom_module_path`, which requires a
Package — so anything that exports a module-owned document to files threw
"Package must be set for custom Module Upande Irrigation".

The visible symptom was that the Smart Irrigation workspace could not be saved
at all: Workspace.on_update() exports the record to its module folder, hit that
check and rolled the save back. Same for any doctype edited through the UI.

The module lives in an installed app, so custom = 0 is simply the correct state.
"""

import frappe

MODULE = "Upande Irrigation"
APP = "upande_irrigation"


def execute():
    if not frappe.db.exists("Module Def", MODULE):
        return

    current = frappe.db.get_value("Module Def", MODULE, ["custom", "app_name"], as_dict=True)
    if not current:
        return

    updates = {}
    if current.get("custom"):
        updates["custom"] = 0
    if current.get("app_name") != APP:
        updates["app_name"] = APP

    if not updates:
        return

    frappe.db.set_value("Module Def", MODULE, updates, update_modified=False)
    frappe.db.commit()
    frappe.clear_cache()
