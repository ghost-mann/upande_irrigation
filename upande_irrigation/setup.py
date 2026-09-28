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
# Animated SVG, the sibling of upande_crm's envelope mark: a tap turns on, water
# fills the badge and the Upande arrow rises out of it, once, then rests as the
# plain Upande badge. The desk tile and the sidebar header draw it through <img>,
# where its CSS animation plays. The /apps launcher and the www page keep the PNG.
LOGO = "/assets/upande_irrigation/images/upande-irrigation-logo.svg"
LEGACY = "Smart Irrigation"


def after_install():
	after_migrate()


def after_migrate():
	ensure_roles()
	ensure_agronomy_defaults()
	seed_block_profiles()
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
	# Only ours: another app shipping a workspace of that name must not be
	# deleted (and re-created) on every migrate.
	if frappe.db.exists("Workspace", {"name": LEGACY, "module": ["in", ["Upande Irrigation", ""]]}):
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


# ── Water balance ───────────────────────────────────────────────
# Starting values for the daily balance (engine/balance.py). They are seeded
# only into empty tables and blank fields, so anything calibrated on site stays.

SCALAR_DEFAULTS = {
	"rain_loss_mm": 2.0, "rain_efficiency": 0.9, "fallback_epan_mm": 4.0, "depletion_fraction": 0.5, "irrometer_weight": 0.5,
	"min_run_hours": 0.5, "max_run_hours_per_day": 12.0, "irrigate_ahead_days": 1,
	"default_soil_texture": "Loam", "default_emitters_per_tree": 1.0, "default_emitter_flow_lph": 70.0,
	"default_application_efficiency": 0.9, "default_depletion_fraction": 0.5,
	"default_window_start": "06:00:00", "default_window_end": "18:00:00",
}
SOILS = [("Sand", 60), ("Loamy sand", 90), ("Sandy loam", 120), ("Loam", 160), ("Silt loam", 190), ("Clay loam", 170), ("Clay", 150)]
AGE_BANDS = [
	(0, 2, 0.3, 20, [0.45] * 12),
	(3, 4, 0.45, 45, [0.60] * 12),
	# Mature avocado: higher use through the Jan–Mar dry season.
	(5, 999, 0.6, 70, [0.80, 0.80, 0.80] + [0.75] * 9),
]
TENSION = [(0, 0.0), (10, 0.1), (30, 0.5), (60, 0.8), (100, 1.0)]
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def ensure_agronomy_defaults():
	if not frappe.db.exists("DocType", "Irrigation Age Band"):
		return
	s = frappe.get_single("Irrigation Settings")
	changed = False
	for field, value in SCALAR_DEFAULTS.items():
		if s.meta.has_field(field) and s.get(field) in (None, ""):
			s.set(field, value)
			changed = True
	if s.meta.has_field("kpan") and not s.get("kpan"):
		# The old settings carried the right figure as default_kpan; the weekly
		# engine used pan_to_crop_coefficient (0.95) instead.
		s.kpan = float(s.get("default_kpan") or 0.75)
		changed = True
	if not s.get("soil_textures"):
		for texture, awc in SOILS:
			s.append("soil_textures", {"texture": texture, "awc_mm_per_m": awc})
		changed = True
	if not s.get("age_bands"):
		for lo, hi, root, canopy, kcs in AGE_BANDS:
			s.append("age_bands", {"age_from": lo, "age_to": hi, "root_depth_m": root, "canopy_pct": canopy,
			                       **{f"kc_{m}": kc for m, kc in zip(MONTHS, kcs)}})
		changed = True
	if not s.get("tension_points"):
		for cb, frac in TENSION:
			s.append("tension_points", {"centibars": cb, "depletion_fraction": frac})
		changed = True
	if changed:
		s.flags.ignore_mandatory = True
		s.save(ignore_permissions=True)


def _ring_area_m2(ring):
	"""Planar area of a lon/lat ring (shoelace on a local equirectangular projection).
	Accurate to well under 1% at block scale near the equator."""
	import math

	if len(ring) < 3:
		return 0.0
	lat0 = math.radians(sum(p[1] for p in ring) / len(ring))
	m_lon = 111320.0 * math.cos(lat0)
	m_lat = 110574.0
	pts = [(p[0] * m_lon, p[1] * m_lat) for p in ring]
	return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2.0


def _geometry_props(raw):
	"""area_m2 / tree_count for a block from its Raw GeoJSON: the properties the
	Lokitela outlines carry, else the polygon's own area (the Endebess outlines
	have geometry but no properties)."""
	try:
		geo = json.loads(raw or "")
	except (TypeError, ValueError):
		return {}
	feats = geo.get("features") if isinstance(geo, dict) and geo.get("type") == "FeatureCollection" else [geo]
	area = 0.0
	for f in feats or []:
		props = (f or {}).get("properties") or {}
		if props.get("area_m2") or props.get("tree_count"):
			return props
		g = (f or {}).get("geometry") or (f if (f or {}).get("type") in ("Polygon", "MultiPolygon") else {})
		polys = [g.get("coordinates")] if g.get("type") == "Polygon" else (g.get("coordinates") or []) if g.get("type") == "MultiPolygon" else []
		for poly in polys:
			if poly:
				area += _ring_area_m2(poly[0]) - sum(_ring_area_m2(h) for h in poly[1:])
	return {"area_m2": area} if area > 0 else {}


def seed_block_profiles():
	"""One Irrigation Block Profile per block warehouse; fills area and tree count
	from the block geometry where the profile leaves them blank. Never overwrites."""
	if not frappe.db.exists("DocType", "Irrigation Block Profile"):
		return
	meta = frappe.get_meta("Warehouse")
	filters = {"is_group": 0, "disabled": 0}
	if frappe.db.exists("Warehouse Type", "Block"):
		filters["warehouse_type"] = "Block"
	else:
		return
	fields = ["name"] + (["custom_raw_geojson"] if meta.has_field("custom_raw_geojson") else [])
	for wh in frappe.get_all("Warehouse", filters=filters, fields=fields, limit_page_length=0):
		props = _geometry_props(wh.get("custom_raw_geojson"))
		area = round(float(props.get("area_m2") or 0) / 10000.0, 2) or None
		trees = int(props.get("tree_count") or 0) or None
		if frappe.db.exists("Irrigation Block Profile", wh.name):
			doc = frappe.get_doc("Irrigation Block Profile", wh.name)
			dirty = False
			if not doc.area_ha and area:
				doc.area_ha, dirty = area, True
			if not doc.tree_count and trees:
				doc.tree_count, dirty = trees, True
			if dirty:
				doc.save(ignore_permissions=True)
			continue
		frappe.get_doc({"doctype": "Irrigation Block Profile", "block": wh.name, "area_ha": area,
		                "tree_count": trees, "is_active": 1}).insert(ignore_permissions=True)
