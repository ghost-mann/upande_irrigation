"""Server-side context for /irrigation-control.

Lists valves grouped by tank with live on/off state and operator override
controls. Data is polled by client JS from upande_irrigation.api.valves.
"""

import frappe

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/irrigation-control"
		raise frappe.Redirect

	context.title = "Irrigation Control"
	context.full_width = True
	context.csrf_token = frappe.local.session.get("csrf_token") if frappe.local.session else ""
	context.current_user = frappe.session.user
