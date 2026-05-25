"""Server-side context for /meniscus dashboard.

This page is the smart-irrigation operator dashboard for Lokitela (and any
other farm with `is_irrigation_farm=1`). The HTML in meniscus.html is a
single-page app that pulls data via /api/method/upande_irrigation.api.weather.fetch.

Was previously stored as a Web Page DocType row named `farm-dashboard`.
"""

import frappe

no_cache = 1   # data is live; don't cache the shell


def get_context(context):
	# AJAX calls require an authenticated session — redirect Guests so we
	# never render a page that's going to 403 its own data fetches.
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/meniscus"
		raise frappe.Redirect

	context.title = "Meniscus"
	context.full_width = True
	context.csrf_token = frappe.local.session.get("csrf_token") if frappe.local.session else ""
	context.current_user = frappe.session.user
