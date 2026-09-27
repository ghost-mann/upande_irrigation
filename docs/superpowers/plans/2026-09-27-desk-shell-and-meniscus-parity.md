# Desk Shell, Meter Doctypes and Meniscus Parity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make upande_irrigation on v16 a complete, Travel-style desk app: dependencies installed, a visible Upande
app tile, a tile-grid workspace and sidebar covering every irrigation doctype, adopted meter doctypes, and a
`/upande-irrigation` dashboard with every Meniscus function.

**Architecture:** The desk shell copies `upande_travel/setup.py` (after_install and after_migrate upsert the
Custom HTML Block and the Desktop Icon, and clean up legacy rows). The meter doctypes become code doctypes. The
dashboard gains two backend endpoints (`valves.set_override_bulk`, `sensors.fleet`, `weather.sections`) and
the matching view changes. The map moves to Esri basemaps (user preference, memory `upande-maps-esri-maplibre`).

**Tech Stack:** Frappe 16 / ERPNext 16, Python, vanilla ES modules, MapLibre GL 5, Three.js 0.160.

**Spec:** `docs/superpowers/specs/2026-09-27-desk-shell-and-meniscus-parity-design.md`

## Global Constraints

- Site: `webstore.localhost` (bench `/home/austin/frappe-v16-bench`). Tests: `bench --site webstore.localhost run-tests --app upande_irrigation --module <mod>`.
- Valve override vocabulary is `Auto`, `Forced Open`, `Forced Closed` (the existing `_VALID_OVERRIDES`). The spec's `auto|open|closed` means these.
- `required_apps = ["erpnext", "upande_core"]`.
- Logo: `/assets/upande_irrigation/images/upande-logo.png` (the default Upande logo, the same bytes as Travel's).
- Mobi Water Reading is excluded.
- Map basemaps: Esri raster (World_Imagery / hybrid / World_Street_Map), with no `glyphs` in the style, HTML markers for labels, satellite `maxzoom: 18`, and map `maxZoom: 19`.

## Review Focus

1. A site without `upande_sensors` (no `Sensor Readings`): `sensors.fleet` must return the empty shape, not raise. The test asserts the `_guard` default when the table is missing.
2. A bulk override with no filter at all (`valves=None, section=None, farm=None`) must be refused for anything other than an explicit "all" flag. Otherwise one mis-click forces every valve on every farm. Test: calling it with no scope throws.
3. A Guest or a user without write on Tank And Valve calling `set_override_bulk` gets PermissionError, and no row changes.
4. Warehouse without `custom_raw_geojson` (upande_scp not installed): the map must still render valves. The Warehouse fields request must not include a column that doesn't exist. The JS checks `frappe.get_meta` through an API flag: `valves.geojson` returns block polygons server-side, guarded by `frappe.get_meta("Warehouse").has_field`.
5. Migrate run twice: the Desktop Icon, the navigation block and the legacy cleanup are idempotent. `hidden` set by a user is preserved.

---

### Task 1: Dependencies and site

**Files:** Modify `upande_irrigation/hooks.py` (required_apps).

- [ ] `required_apps = ["erpnext", "upande_core"]`
- [ ] `bench --site webstore.localhost install-app upande_core`, then `bench --site webstore.localhost migrate`
- [ ] Verify `frappe.db.exists("DocType","Farm")`.
- [ ] Commit.

### Task 2: Desk shell (workspace, sidebar, Desktop Icon, navigation block)

**Files:**
- Create: `upande_irrigation/setup.py`, `upande_irrigation/custom_html_block/irrigation_navigation.{html,css,js}`,
  `upande_irrigation/desktop_icon/upande_irrigation.json`,
  `upande_irrigation/upande_irrigation/workspace/upande_irrigation/upande_irrigation.json`,
  `upande_irrigation/tests/test_desk_shell.py`
- Delete: `upande_irrigation/upande_irrigation/workspace/smart_irrigation/`
- Modify: `workspace_sidebar/upande_irrigation.json` (icons, sections, Views), `hooks.py`
  (after_install → `upande_irrigation.setup.after_install`, after_migrate → `upande_irrigation.setup.after_migrate`,
  add_to_apps_screen route `/desk/upande-irrigation`, drop the Custom HTML Block fixture, add the redirect
  `/app/smart-irrigation` → `/desk/upande-irrigation`), `fixtures/custom_html_block.json` (delete),
  `www/upande-irrigation.html` and `shell.js` (the Records/Desk links → `/desk/upande-irrigation`).

**Interfaces:** Produces `setup.after_install()`, `setup.after_migrate()`, `setup.NAVIGATION_BLOCK = "Irrigation Navigation"`, `setup.DESKTOP_ICON = "Upande Irrigation"`.

- [ ] Write `test_desk_shell.py`:

```python
import frappe
from frappe.tests import IntegrationTestCase
from upande_irrigation import setup


class TestDeskShell(IntegrationTestCase):
	def test_desktop_icon_is_standard_and_opens_sidebar(self):
		setup.after_migrate()
		icon = frappe.get_doc("Desktop Icon", "Upande Irrigation")
		self.assertEqual(icon.standard, 1)
		self.assertEqual(icon.link_type, "Workspace Sidebar")
		self.assertEqual(icon.link_to, "Upande Irrigation")
		self.assertEqual(icon.logo_url, "/assets/upande_irrigation/images/upande-logo.png")

	def test_user_hidden_flag_survives_migrate(self):
		setup.after_migrate()
		frappe.db.set_value("Desktop Icon", "Upande Irrigation", "hidden", 1)
		setup.after_migrate()
		self.assertEqual(frappe.db.get_value("Desktop Icon", "Upande Irrigation", "hidden"), 1)

	def test_navigation_block_and_workspace(self):
		setup.after_migrate()
		self.assertTrue(frappe.db.exists("Custom HTML Block", "Irrigation Navigation"))
		ws = frappe.get_doc("Workspace", "Upande Irrigation")
		self.assertIn("Irrigation Navigation", ws.content)

	def test_legacy_desk_removed(self):
		setup.after_migrate()
		self.assertFalse(frappe.db.exists("Workspace", "Smart Irrigation"))
		self.assertFalse(frappe.db.exists("Workspace Sidebar", "Smart Irrigation"))

	def test_every_sidebar_doctype_exists(self):
		sb = frappe.get_doc("Workspace Sidebar", "Upande Irrigation")
		for item in sb.items:
			if item.type == "Link" and item.link_type == "DocType":
				self.assertTrue(frappe.db.exists("DocType", item.link_to), item.link_to)
```

- [ ] Run it; expect failures (no `upande_irrigation.setup`).
- [ ] Implement `setup.py`: `ensure_roles()` (moved from install.py; install.py keeps a shim), `ensure_navigation_block()`,
  `ensure_desktop_icon()`, `remove_legacy_desk()` and `sync_desktop_layouts()`, all copied from upande_travel with names swapped.
  `remove_legacy_desk()` deletes Workspace "Smart Irrigation" and any Workspace Sidebar "Smart Irrigation" with `standard=0`.
- [ ] Navigation block tiles:
  - **Dashboard**: Irrigation Dashboard `/upande-irrigation` (primary), Field Map `#map`, Valve Control `#control`, Irrigation Now `#now`
  - **Planning**: Irrigation Planner, Irrigation Scheduler, Scheduler Run
  - **Field Data**: Weather Reading, Reservoir Pumping Record, Water Transfer, Water Meter Reading, Electricity Meter Reading
  - **Setup**: Irrigation Settings, Irrigation Pump Profile, Tank And Valve, Farm, Warehouse

  The same data-doctype hiding JS as Travel.
- [ ] Sidebar items, each with a lucide icon:
  - Home → Workspace Upande Irrigation; Irrigation Dashboard (URL)
  - Planning: Irrigation Planner (calendar-check), Irrigation Scheduler (clock), Scheduler Runs (history)
  - Field Data: Weather Readings (cloud-sun), Reservoir Pumping (waves), Water Transfers (arrow-left-right), Water Meter Readings (gauge), Electricity Meter Readings (zap)
  - Setup: Irrigation Settings (settings), Pump Profiles (activity), Tanks and Valves (droplet), Farm (tractor), Warehouse (warehouse)
  - Views: Overview, Planning, Weather, Compare, IoT, Water & Energy, Field Map, Irrigation Now, Valve Control — URLs `/upande-irrigation#<id>`

  Irrometer Reading is a child table, so it is not linked.
- [ ] Run `bench --site webstore.localhost migrate`, then the tests pass.
- [ ] Commit.

### Task 3: Water Meter Reading and Electricity Meter Reading

**Files:** Create `upande_irrigation/upande_irrigation/doctype/{water_meter_reading,electricity_meter_reading}/{__init__.py,<name>.json,<name>.py,<name>.js}` and `tests/test_meter_readings.py`. Modify `migration/pull.py`/`generate.py` export lists.

**Interfaces:** Produces controllers with `validate()` → `set_previous_reading()` (only when `previous_reading` is empty) and `compute_units()`.

- [ ] Test:

```python
class TestMeterReadings(IntegrationTestCase):
	def test_units_used_computed(self):
		d = frappe.get_doc({"doctype": "Electricity Meter Reading", "date": "2026-09-01",
			"previous_reading": 100, "new_reading": 130}).insert()
		self.assertEqual(d.units_used, 30)

	def test_negative_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Electricity Meter Reading", "date": "2026-09-01",
				"previous_reading": 100, "new_reading": 90}).insert()

	def test_previous_defaults_from_last_submitted(self):
		a = frappe.get_doc({"doctype": "Electricity Meter Reading", "date": "2026-09-01",
			"previous_reading": 0, "new_reading": 500}).insert(); a.submit()
		b = frappe.get_doc({"doctype": "Electricity Meter Reading", "date": "2026-09-02",
			"new_reading": 520}).insert()
		self.assertEqual(b.previous_reading, 500)
		self.assertEqual(b.units_used, 20)
```

  Plus a water variant scoped by `irrigation_section`.
- [ ] JSON from the v15 schema (autoname `format:WMR-{YYYY}-{MM}-{#####}` / `EMR-…`, `is_submittable 1`, `custom 0`,
  module Upande Irrigation, perms System Manager / Agriculture Manager (full) and Irrigation User (create/read/write/submit)).
- [ ] Implement the controllers, migrate, and the tests pass. Add both doctypes to the migration export lists and rerun `test_pull`/`test_migration_generate`.
- [ ] Commit.

### Task 4: Bulk valve overrides

**Files:** Modify `api/valves.py`; create `tests/test_valves_bulk.py`.

**Interfaces:** `set_override_bulk(state, valves=None, section=None, farm=None, all_valves=0) -> {ok, state, updated:[names], count}`. `valves` is a JSON list or a Python list. `section` matches the valve block's `parent_warehouse`.

- [ ] Tests: an explicit list is updated; farm scoping only touches that farm; no scope and no `all_valves` → throws; an invalid state → throws; Guest → PermissionError, with nothing changed.
- [ ] Implement: validate the state and resolve the target names via `frappe.get_all("Tank And Valve", {"asset_type":"Valve", …})`. Section → blocks through `Warehouse.parent_warehouse`. Check `frappe.has_permission("Tank And Valve","write")` and throw otherwise. For each target, `get_doc`, set `manual_state`, then `save()`. Commit once.
- [ ] Commit.

### Task 5: Map: Esri basemap, farm scope, popup controls, locate a valve

**Files:** Modify `api/valves.py` (new `blocks_geojson(farm=None)`), `public/js/dashboard/view_map.js`, `view_control.js`, and `public/css/views.css`.

**Interfaces:** `valves.blocks_geojson(farm=None) -> FeatureCollection`. It returns an empty collection with `meta.unavailable=True` when Warehouse has no `custom_raw_geojson`. view_map reads `#map?valve=<name>`.

- [ ] Test (in test_valves_bulk.py): `blocks_geojson()` returns a FeatureCollection dict, and doesn't raise on a site without the field.
- [ ] view_map:
  - Replace BASEMAP_STYLE with an inline Esri raster style: Satellite, Hybrid and Streets sources, and a segmented toggle in the card head.
  - Remove the symbol label layer, because there are no glyphs. Block labels become HTML markers, shown at zoom ≥ 16.
  - Pass `farm` to `valves.geojson` and `blocks_geojson`. Rebuild the map when the farm filter changes.
  - The popup shows Auto/On/Off buttons that call `set_override`, then `paintState()`.
  - `#map?valve=X` flies to that valve and opens its popup.
- [ ] view_control:
  - Each valve card gets a **Show on map** link → `#map?valve=<name>`.
  - A bulk bar gets **Close all**, **Reset all to Auto** and **Open all**, scoped to the current farm filter and each behind a `confirm()` with the count. Each group card head gets Auto/On/Off group buttons (`valves:[…]`).
- [ ] Commit.

### Task 6: IoT fleet overview

**Files:** Modify `api/sensors.py` (add `fleet`), `view_iot.js`; create `tests/test_sensors_fleet.py`.

**Interfaces:** `sensors.fleet(days=30, site_name="", sensor_type="") -> {meta, kpis:{sensors, readings, avg_battery, avg_rssi, online}, devices:[{deveui, sensor_name, sensor_type, site_name, units, latest_value, latest_at, stale, hours_silent, min_value, max_value, battery, rssi, snr, reading_count, spark:[…]}], readings:[≤200 {timestamp, sensor_name, sensor_type, value, units, battery, rssi, snr}]}`. It is `_guard`-wrapped, with an empty default.

- [ ] Test: the shape keys are present; with Sensor Readings absent (or zero rows) the empty default comes back with `meta.unavailable` or zero counts, and no exception.
- [ ] Implement in SQL: one aggregate per (deveui, sensor_type), the latest row per device through a join on MAX(timestamp), a spark of 24 buckets over the window, and the last 200 raw rows.
- [ ] view_iot: above the filters, show the fleet KPIs (Sensors, Readings, Avg battery, Avg RSSI with its quality label, Online). Show a card grid (click → select the sensor), then the existing drill-down. Below it, a Recent Readings table with SNR. Add SNR to the Battery KPI subline.
- [ ] Commit.

### Task 7: Weather: sections table and temperature band

**Files:** Modify `api/weather.py` (add `sections(farm="")`), `view_weather.js`; create `tests/test_weather_sections.py`.

**Interfaces:** `weather.sections(farm="") -> {sections:[{section, blocks, valves, coverage_pct, application_rate_mm_hr, active}]}`, built from Irrigation Shift Block rows (child of the Irrigation Scheduler single). Section = the block's `parent_warehouse`. The valve count comes from Tank And Valve by block.

- [ ] Test: insert a scheduler shift block row plus a valve in a test, and assert that section's aggregate. An empty site → `{"sections": []}`.
- [ ] view_weather: a new "Irrigation Sections" card (Section, Blocks, Valves, Coverage %, mm/hr, Active/Idle). Temperature chart: `band: {lo, hi, color: T.heat}`.
- [ ] Commit.

### Task 8: Shell polish: guide, Refresh, 2-year period, flow dots, meter columns

**Files:** `shell.js`, `www/upande-irrigation.html`, `public/css/shell.css`/`views.css`, `view_resources.js`.

- [ ] PERIODS gains `[730, "Last 2 years"]`.
- [ ] Topbar: a Refresh button → `shell.safeRefresh()`, and a Guide `?` button → a drawer (backdrop, Esc, close) with one paragraph per view.
- [ ] Resources: the water table gets Prev and New columns, and the electricity table gets Prev and New columns (the data is already returned). Restore the flow-dot CSS animation on the flow diagram.
- [ ] Commit.

### Task 9: Verification

- [ ] `bench --site webstore.localhost run-tests --app upande_irrigation`: the full suite is green (note any failures that predate this work, with evidence).
- [ ] `bench build --app upande_irrigation` if needed; clear the cache.
- [ ] Headless browser: log in, visit `/desk/upande-irrigation`, `/upande-irrigation#overview` … `#control`, and assert there are no uncaught console errors; screenshot the workspace and the map.
- [ ] Update the memory index with the desk-shell pattern if it's new.
