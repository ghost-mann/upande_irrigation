"""Server-side context for /irrigation-now.

Shows, per farm and section, the shift currently being irrigated with a
countdown and progress bar, plus the next few upcoming shifts in queue.
Data is polled by client JS from upande_irrigation.api.scheduler.live_sections.
"""

import frappe

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/irrigation-now"
		raise frappe.Redirect

	context.title = "Irrigation Now"
	context.full_width = True
	context.csrf_token = frappe.local.session.get("csrf_token") if frappe.local.session else ""
	context.current_user = frappe.session.user
