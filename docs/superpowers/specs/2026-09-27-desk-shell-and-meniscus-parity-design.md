# Upande Irrigation — Desk Shell, Meter Doctypes and Meniscus Parity

**Date:** 2026-09-27
**Status:** Approved. User delegated the remaining approvals: "im afk finish this".

## Why

On the v16 bench site `webstore.localhost`, the user found:

1. **"DocType Farm not found."** Farm now lives in `upande_core`, which is not installed
   on that site. `upande_irrigation` never declares `required_apps`, so nothing forced
   the dependency.
2. **The app icon is invisible.** The logo file is the correct Upande logo (the same image as
   upande_travel's) and it is served with a 200. The problem is the tile: its `Desktop Icon` row was created
   ad hoc (`standard: 0`, `link_type: External`, `link: /app/smart-irrigation`).
   upande_travel and upande_webstore ship `desktop_icon/<app>.json` (`standard: 1`,
   `link_type: Workspace Sidebar`) and refresh it on migrate.
3. **The workspace does not look like Upande Travel's.** Travel's workspace body is a
   single Custom HTML tile grid ("Travel Navigation"), and its sidebar has an icon on
   every item, ending in a **Views** section of deep links into the app page.
   Irrigation still has the v15 shortcuts-and-cards workspace. It also has a stray auto-generated
   "Smart Irrigation" sidebar (`standard: 0`) next to the shipped "Upande Irrigation" one.
4. **Missing doctypes.** The live v15 workspace (read over REST 2026-09-27, last modified
   2026-06-19) matches the git copy: 11 doctypes, plus a dead `Irrigation Block` link
   (no such doctype exists on v15 either). The user ruled that **`upande_kaitet` no longer
   exists**: its irrigation doctypes live in `upande_irrigation`. So the following are
   adopted, **excluding Mobi Water Reading** (user's instruction):

   | DocType (v15 module Upande Kaitet) | v15 records | Shape |
   |---|---|---|
   | Water Meter Reading | 82 | submittable, `format:WMR-{YYYY}-{MM}-{#####}`; `date` Datetime, `irrigation_section` → Warehouse, `previous_reading`, `new_reading`, `units_used` |
   | Electricity Meter Reading | 310 | submittable, `format:EMR-{YYYY}-{MM}-{#####}`; `date` Date, `previous_reading`, `new_reading`, `units_used` |

   Irrigation Pump Profile was already adopted by the 2026-09-13 migration.
5. **"Everything in Meniscus should be in the new dashboard."** A feature-by-feature
   inventory of the live v15 `/meniscus` (scratchpad `meniscus_gap.md`) shows v16
   `/upande-irrigation` already covers every tab. The live page is *older* than the file
   the July port used. The valve map exists (`view_map.js`: MapLibre plus Three.js). What
   remains are the gaps below. The user asked for all of them: "even the graphs, weather
   section, all sections basically".

## Scope: four phases in one spec

### Phase 1 — Site and dependencies

- `hooks.py`: `required_apps = ["erpnext", "upande_core"]`.
- Install `upande_core` on `webstore.localhost` (`bench --site webstore.localhost
  install-app upande_core`), then `migrate`.
- **No production data is copied.** Map/chart verification uses whatever the site holds,
  plus test fixtures created inside tests (rolled back).

### Phase 2 — Desk shell (the upande_travel pattern)

- **Workspace renamed** `Smart Irrigation` → **`Upande Irrigation`**, matching the sidebar
  name (Travel: workspace `Upande Travel` ↔ sidebar `Upande Travel`). The body is one
  `custom_block` **"Irrigation Navigation"** (col 12). Its shortcuts list the tiles' targets.
  Old `workspace/smart_irrigation/` is removed.
- **`setup.py`** (called from `after_install` and a new `after_migrate`), modelled on
  `upande_travel/setup.py`:
  - `ensure_navigation_block()` upserts Custom HTML Block "Irrigation Navigation" from
    `custom_html_block/irrigation_navigation.{html,css,js}`. The tiles are grouped as
    *Dashboard*, *Planning*, *Field Data* and *Setup*, each with a count and a link.
  - `ensure_desktop_icon()` refreshes Desktop Icon "Upande Irrigation": `standard 1`,
    `icon_type App`, `link_type Workspace Sidebar`, `link_to Upande Irrigation`,
    `logo_url /assets/upande_irrigation/images/upande-logo.png`, keeping `hidden`.
  - `remove_legacy_desk()` deletes the Workspace `Smart Irrigation` and the
    non-standard Workspace Sidebar `Smart Irrigation`, if present.
- Ship `desktop_icon/upande_irrigation.json`, the same shape as Travel's.
- `add_to_apps_screen.route` → `/desk/upande-irrigation`.
- The `Smart Irrigation Dashboard` Custom HTML Block fixture is dropped (it belonged to the
  old workspace body); the navigation block replaces it.
- **Sidebar** `workspace_sidebar/upande_irrigation.json`, with an icon on every item:
  - Home (Workspace) · Irrigation Dashboard (`/upande-irrigation`)
  - **Planning**: Irrigation Planner, Irrigation Scheduler, Scheduler Runs
  - **Field Data**: Weather Readings, Irrometer Readings (the Irrometer child table is not
    linked, because it is not listable; see Irrigation Planner), Reservoir Pumping, Water Transfers, Water Meter
    Readings, Electricity Meter Readings
  - **Setup**: Irrigation Settings, Pump Profiles, Tanks and Valves, Farm, Warehouse
  - **Views**: Overview, Map, Control, Now, Planning, Weather, Compare, IoT, Water & Energy,
    each a URL `/upande-irrigation#<view>`
- The `Irrigation Block` dead link is not carried over.

### Phase 3 — Meter doctypes adopted

- `upande_irrigation/upande_irrigation/doctype/water_meter_reading/` and
  `electricity_meter_reading/`: `custom 0`, module Upande Irrigation, the same fields and
  autoname as v15, `is_submittable 1`. The controller computes
  `units_used = new_reading - previous_reading` and rejects a negative value. It defaults
  `previous_reading` from the last submitted reading (per section for water).
  Permissions: System Manager plus the roles already used by the app's other doctypes.
- Migration: add both to `migration/pull.py` / `generate.py` export lists, so the Phase-2
  cutover runbook moves their 82 and 310 records with their names preserved (`insert_preserving`).
- `api/resources.py` already queries these doctypes by name. Its existence guards stay.

### Phase 4 — Dashboard parity

Ranked, most important first:

1. **Bulk valve actions.** `api/valves.set_override_bulk(state, valves=None, section=None,
   farm=None)`. `state` ∈ `auto|open|closed`, the same vocabulary as `set_override`. It applies
   to an explicit valve list, or to every valve in a section and/or farm. It needs the same
   permission as `set_override`, runs in one transaction, and returns the per-valve result.
   UI in `view_control.js`: **Close all**, **Reset all to Auto**, and a per-section
   **Open/Closed/Auto** group control, each behind a `confirm()` that names the valve
   count.
2. **Map ↔ control link.** The map popup gets Auto/Open/Closed buttons (they call
   `set_override`, then re-light). Each valve card in Control gets **Show on map**
   (`#map?valve=<name>`), and the map flies to and highlights it.
3. **Map follows the farm filter.** `valves.geojson(farm=…)` filters valves and block
   polygons by `Warehouse.custom_farm`. `view_map.js` passes `farm`.
4. **IoT fleet overview.** `api/sensors.fleet(days, site_name, sensor_type)` returns
   KPIs (sensor count, readings, average battery, average RSSI with a quality label, count online),
   one entry per device (latest value and units, Live/Stale at 6 h, time since last reading, min/max,
   sparkline series, battery/RSSI/**SNR**, readings in window), and `readings` (the last 200
   raw rows). `view_iot.js` renders the KPIs, a card grid and a Recent Readings table
   above the existing single-sensor drill-down. Clicking a card selects that sensor.
5. **Irrigation Sections table** (Weather view). The v15 columns `plant_density`,
   `micro_output` and `soil_type` exist in no schema on either site, which is why v15 showed "—".
   The table is rebuilt from real data: Section, Blocks, Valves (a count of Tank And Valve),
   Coverage % and Application rate mm/hr (from Irrigation Shift Block), and Active/Idle
   (any active shift). It is served by `api/weather.sections(farm)`.
6. **Guide drawer.** A `?` button in the topbar opens a drawer (Esc/backdrop closes it) with
   one short section per view, rewritten for v16.
7. **Meter tables.** Prev and New columns in the Water and Electricity tables
   (`view_resources.js`). `api/resources.py` returns `previous_reading`/`new_reading`.
8. **Temperature min–max band.** `view_weather.js` passes `band` to `charts.mkChart`.
9. **Global Refresh and the 2-year period.** The topbar Refresh button re-runs the active view's
   `load()`. `PERIODS` gains 730.
10. **Flow-diagram dots** (cosmetic). Restore the CSS animation on the Water & Energy flow
    diagram.

Out of scope: the v15 "Station vs Sensor — In Development" placeholder (never functional),
and the fake valve grid and emergency stop (replaced by item 1).

## Testing

- Python: `bench --site webstore.localhost run-tests --app upande_irrigation`. New tests:
  `test_valves_bulk.py` (state applied, permission denied for Guest, farm/section scoping,
  invalid state rejected), `test_meter_readings.py` (units_used computed, negative
  rejected, previous defaulted), `test_sensors_fleet.py` (shape, stale flag, SNR present),
  `test_weather_sections.py`, `test_desk_shell.py` (Desktop Icon standard, links to the sidebar,
  navigation block exists, no `Smart Irrigation` workspace/sidebar, every sidebar DocType
  link resolves to an existing DocType).
- The existing suite must stay green.
- UI: load `/upande-irrigation` on webstore.localhost headless and assert no console
  errors on each view. Check `/desk/upande-irrigation` renders, and the Apps screen shows the tile.

## Risks

- Renaming the workspace breaks bookmarks to `/app/smart-irrigation`. A `website_redirects`
  entry (`/app/smart-irrigation` → `/desk/upande-irrigation`) covers it.
- `upande_core` install on webstore.localhost may pull its own fixtures (Workspace, Farm
  Type); that is expected for any site running irrigation.
