"""Install/migrate setup for the desk shell. Runs on both hooks and is idempotent.

Modelled on upande_travel/setup.py so the two apps look and behave alike on the
Apps screen and in the desk:

- the workspace body is one Custom HTML Block (a tile grid), upserted from
  custom_html_block/irrigation_navigation.{html,css,js};
- the Apps-screen tile is a standard Desktop Icon that opens the sidebar;
- the v15 "Smart Irrigation" workspace, and the sidebar v16 auto-generated for
  it, are removed — the app's workspace is now "Upande Irrigation".

`bench install-app` marks patches as applied without running them, so none of
this lives in patches.txt.
"""

import json
import os

import frappe

from upande_irrigation.install import ensure_roles

# It cannot ship as a module fixture: `Custom HTML Block` is not importable by
# sync_all(), and the workspace JSON only references it by name. Edit the files,
# not the block in the desk (migrate overwrites it).
NAVIGATION_BLOCK = "Irrigation Navigation"
DESKTOP_ICON = "Upande Irrigation"
WORKSPACE = "Upande Irrigation"
LOGO = "/assets/upande_irrigation/images/upande-logo.png"
LEGACY = "Smart Irrigation"


def after_install():
	after_migrate()


def after_migrate():
	ensure_roles()
	ensure_navigation_block()
	ensure_desktop_icon()
	remove_legacy_desk()
	sync_desktop_layouts()


def ensure_navigation_block():
	if not frappe.db.exists("DocType", "Custom HTML Block"):
		return
	base = os.path.join(os.path.dirname(__file__), "custom_html_block", "irrigation_navigation")
	src = {}
	for field, ext in (("html", "html"), ("style", "css"), ("script", "js")):
		with open(f"{base}.{ext}", encoding="utf-8") as f:
			src[field] = f.read()
	if frappe.db.exists("Custom HTML Block", NAVIGATION_BLOCK):
		doc = frappe.get_doc("Custom HTML Block", NAVIGATION_BLOCK)
	else:
		doc = frappe.new_doc("Custom HTML Block")
		doc.name = NAVIGATION_BLOCK
	doc.update(src)
	doc.private = 0
	doc.set("roles", [])
	doc.save(ignore_permissions=True)


def ensure_desktop_icon():
	"""The Apps-screen tile renders from `Desktop Icon` alone; the hook and the
	workspace don't create it. desktop_icon/upande_irrigation.json covers a fresh
	sync; this refreshes an existing row (keeping `hidden` if a user hid it) and
	replaces the ad-hoc External-link row older installs created."""
	if not frappe.db.exists("DocType", "Desktop Icon"):
		return
	if frappe.db.exists("Desktop Icon", DESKTOP_ICON):
		doc = frappe.get_doc("Desktop Icon", DESKTOP_ICON)
	else:
		doc = frappe.new_doc("Desktop Icon")
		doc.name = DESKTOP_ICON
		doc.hidden = 0
	wanted = {
		"label": DESKTOP_ICON,
		"standard": 1,
		"app": "upande_irrigation",
		"icon_type": "App",
		# A "Workspace Sidebar" icon opens the sidebar's first link (Home → the
		# workspace) in the same tab. An "External" link is origin-prefixed, so
		# the Apps screen treats it as http and opens a new tab.
		"link_type": "Workspace Sidebar",
		"link_to": WORKSPACE,
		# Not used for navigation (link_type wins), but Frappe's
		# create_desktop_icons_from_workspace() calls .startswith() on an App
		# icon's `link`, and a None there aborts icon generation for every
		# workspace after this one.
		"link": "/desk/upande-irrigation",
		"logo_url": LOGO,
		"bg_color": "gray",
	}
	# Save only on a real change: in developer mode every save of a standard
	# Desktop Icon re-exports desktop_icon/upande_irrigation.json with a new
	# `modified`, dirtying the working tree on every migrate.
	if not doc.is_new() and all(doc.get(k) == v for k, v in wanted.items()):
		return
	doc.update(wanted)
	doc.save(ignore_permissions=True)


def remove_legacy_desk():
	"""Drop the v15 workspace and the non-standard sidebar v16 generated for it.

	Only the auto-generated sidebar (standard=0) is removed — a standard one
	would belong to some app's shipped files, which this app does not own.
	"""
	if frappe.db.exists("Workspace", LEGACY):
		frappe.delete_doc("Workspace", LEGACY, ignore_permissions=True, force=True)
	if frappe.db.exists("Workspace Sidebar", {"name": LEGACY, "standard": 0}):
		frappe.delete_doc("Workspace Sidebar", LEGACY, ignore_permissions=True, force=True)
	# The old workspace body, formerly a fixture; the navigation block replaces it.
	if frappe.db.exists("Custom HTML Block", "Smart Irrigation Dashboard"):
		frappe.delete_doc("Custom HTML Block", "Smart Irrigation Dashboard", ignore_permissions=True, force=True)
	# The auto-generated workspace tile for the old name, if any.
	for name in frappe.get_all(
		"Desktop Icon", filters={"label": LEGACY, "icon_type": "Link", "standard": 0}, pluck="name"
	):
		frappe.delete_doc("Desktop Icon", name, ignore_permissions=True, force=True)


# Fields a Desktop Layout row copies from its Desktop Icon.
_LAYOUT_FIELDS = [
	"name", "label", "bg_color", "link", "link_type", "app", "icon_type", "parent_icon", "icon",
	"link_to", "idx", "standard", "logo_url", "hidden", "restrict_removal", "icon_image",
]


def sync_desktop_layouts():
	"""Put this app's tile into every saved Apps-screen layout.

	Once a user rearranges their Apps screen, Frappe renders their stored
	`Desktop Layout` snapshot (a *copy* of each icon's fields) instead of the
	live icons, so an edited logo or route never reaches them. Only our row is
	touched, keeping the user's placement and every other tile as stored. Same
	approach as upande_travel.setup and upande_crm.apps_screen.
	"""
	if not frappe.db.exists("DocType", "Desktop Layout"):
		return
	icon = frappe.db.get_value("Desktop Icon", DESKTOP_ICON, _LAYOUT_FIELDS, as_dict=True)
	if not icon:
		return
	entry = {**icon, "child_icons": []}
	for name in frappe.get_all("Desktop Layout", pluck="name"):
		doc = frappe.get_doc("Desktop Layout", name)
		try:
			layout = json.loads(doc.layout or "[]")
		except ValueError:
			continue
		if not isinstance(layout, list):
			continue
		kept, placement = [], None
		for row in layout:
			if not isinstance(row, dict):
				continue
			ours = row.get("app") == "upande_irrigation" or row.get("name") in (DESKTOP_ICON, LEGACY)
			if not ours:
				kept.append(row)
			elif placement is None:
				placement = {"idx": row.get("idx"), "parent_icon": row.get("parent_icon"), "hidden": row.get("hidden", 0)}
		if placement is None:
			placement = {"idx": max([r.get("idx") or 0 for r in kept] + [0]) + 1, "parent_icon": None, "hidden": 0}
		kept.append({**entry, **placement})
		if kept != layout:
			doc.layout = json.dumps(kept)
			doc.save(ignore_permissions=True)
