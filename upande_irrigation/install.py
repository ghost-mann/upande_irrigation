"""Install-time seeding for a clean site.

The bug being fixed: every DocPerm this app ships names a Role that no app in
the destination's inventory creates. ERPNext v16 dropped `Agriculture Manager`
and `Agriculture User`, and `Irrigation User` never existed outside the source
site's own `tabRole`. DocPerms import with `ignore_links=True`, so on a clean
site they bind to nothing — Weather Reading in particular would have no DocPerm
that any non-Administrator holds, making it a dead sidebar link for every user.

Roles are created rather than replaced with System Manager because the DocPerms
already encode a real separation (operators log weather; managers plan), and
widening every DocType to System Manager would hand operational write access to
anyone with the admin role. Creating the roles keeps the intended model and
fixes every DocType at once.

`bench install-app` does not run patches, and `bench migrate` does not run
after_install, so the same idempotent function is wired to both.
"""

import frappe

# Roles referenced by the DocPerms in upande_irrigation/doctype/*/*.json.
APP_ROLES = ("Irrigation User", "Agriculture Manager", "Agriculture User")


def ensure_roles():
	"""Create any of APP_ROLES that this site does not already have. Idempotent."""
	created = []
	for role_name in APP_ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		doc = frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1})
		doc.insert(ignore_permissions=True)
		created.append(role_name)

	if created:
		frappe.db.commit()
	return created


def after_install():
	ensure_roles()
