# Upande Irrigation Dashboard — Design

**Date:** 2026-07-31
**Status:** Approved (user delegated remaining approvals: "go with what you'd recommend and finish this")

## Problem

The app's operator-facing surface is three unrelated www pages with three different visual
languages, none of which match the Upande house style used by Task Work Hub:

| Page | Look | Backing |
|---|---|---|
| `/meniscus` | Cool blue-grey SaaS, topbar + horizontal tabs, Space Grotesk | real (`api.weather`, `api.sensors`, `api.resources`) |
| `/irrigation-now` | Slate `templates/web.html` child page | real (`api.scheduler.live_sections`) |
| `/irrigation-control` | Slate `templates/web.html` child page | real (`api.valves`) |

Three further problems compound it:

1. "Meniscus" is not a name anyone uses for this any more — it should be Upande Irrigation.
2. `meniscus.html` is 2,514 lines of HTML+CSS+JS in one file. Its "Compare" tab is an empty
   *In Development* placeholder while the actual Year-on-Year chart is buried at the bottom of
   the Weather tab. Its "Irrigation Control" tab is a **UI simulation** with hardcoded valve
   counts (`23ha: 7, 56ha: 15, 65ha: 14, 70ha: 19`) and a fake `emergencyStop()` — a parallel,
   lying implementation of what `/irrigation-control` does for real.
3. Field operators cannot log a daily Weather Reading without desk access, even though every
   downstream number in the app derives from that one daily form.

## Goals

- One page, `/upande-irrigation`, visually indistinguishable from Task Work Hub.
- All three pages' real functionality behind one sidebar; the simulation deleted.
- Daily Weather Reading entry from the dashboard.
- Files small enough to reason about individually.
- The repo actually owns Weather Reading, and the Irrigation Planner fixture stops lying.

Out of scope (next spec): relocating `Block Type` (module `Custom`), `Blocks List`,
`Irrigation Pump Profile`, `Water Meter Reading`, `Electricity Meter Reading` (module
`Upande Kaitet`) into `Upande Irrigation`.

## Theme

Ported verbatim from `task_work_hub.js`'s `inject_css()` (lines 1317–1559), renamed
`--twh-*` → `--ui-*`, `.twh-*` → `.ui-*`:

```
ink #0a0a0a   ink3 #3a3a34   ink4 #5a5a52   mute #8a8780
bg  #f4f3ef   card #ffffff   hair rgba(10,10,10,0.06)
clay #c25a2e  clay-deep #7c2f16
ok #3f8f4f    warn #d9962e   hot #c4302b    teal #228883
shadow    0 1px 0 rgba(10,10,10,.04), 0 8px 32px -16px rgba(10,10,10,.10)
grad-ink  linear-gradient(135deg,#0a0a0a,#3a3a34)
grad-clay linear-gradient(135deg,#9a3c1e,#e07a4f)
radii     card 18px · kpi/tile 16px · navlink 10px · pill 999px
```

Typography matches desk: `--font-stack` = `"InterVariable","Inter",-apple-system,…` (the exact
string from `frappe/public/scss/espresso/_typography.scss`). Inter is loaded from Google Fonts
since this page is standalone HTML and does not inherit desk's CSS. `--font-stack-mono` is
defined locally (desk never defines it; Task Work Hub falls back to `monospace`).

Structural match: sticky left sidebar with a collapse-to-rail button, `VIEWS` label, nav links
with gradient-ink active state and count badges, a `twh-desklink`-equivalent "Desk" link, then
a collapsible stats block and legend. Page bodies use `.ui-pagehead` (eyebrow + h1 + p),
`.ui-kpis`/`.ui-kpi`, `.ui-card`/`.ui-cardhead`, `.ui-table`, `.ui-btn`, `.ui-chip`, `.ui-sev`.

Collapse state persists in `localStorage` under `ui-irr-rail`.

## Routes

| Old | New | Mechanism |
|---|---|---|
| `/meniscus` | `/upande-irrigation` | `website_redirects` 301 |
| `/irrigation-now` | `/upande-irrigation#now` | `website_redirects` 301 |
| `/irrigation-control` | `/upande-irrigation#control` | `website_redirects` 301 |

Views are hash-routed so the redirects deep-link. Unknown/absent hash → `#overview`.
Old page files are deleted; the redirect is the compatibility layer. `www/meniscus.css` is
deleted outright — nothing references it.

Also updated: the `Smart Irrigation Dashboard` Custom HTML Block fixture (`/meniscus` link) and
the `Smart Irrigation` workspace shortcuts (`/meniscus`, `/irrigation-control`).

## Views

| View | Source | Notes |
|---|---|---|
| Overview | new | KPI tiles + "Needs attention" alert list, from `api.overview.fetch` |
| Weather | meniscus weather tab | + daily Weather Reading entry form |
| Compare | meniscus `#compare-section` | promoted out of the Weather tab; the empty placeholder tab is dropped |
| IoT | meniscus iot tab | unchanged behaviour |
| Water & Energy | meniscus resources tab | unchanged behaviour |
| Map | meniscus irrigation tab (3D map only) | now driven by real valve state |
| Now | `/irrigation-now` | unchanged behaviour |
| Control | `/irrigation-control` | unchanged behaviour |

**Deleted, not ported:** the simulated valve grid (`SECTIONS`, `vState`, `toggleValve`,
`toggleAllValves`, `emergencyStop`, `buildValveGrid`) and the `switchToValve` heuristic that
guessed which real valve a fake switch drove by string-matching names
(`BONDENI|WESA|KINYORO → 70ha`, `DAIRY` splitting on trailing number, etc.). The Control view
already lists real valves keyed by `name`, so the Map view keys its 3D highlights on the same
`name` — no pairing heuristic, and the map reflects `effective_state` from `api.valves.list_states`
instead of a local simulation.

## File layout

```
www/upande-irrigation.html          shell + sidebar markup            ~190 lines
www/upande-irrigation.py            get_context: guest redirect, csrf
public/css/tokens.css               house palette, type, radii        ~60
public/css/shell.css                sidebar, cards, pills, tables     ~330
public/js/dashboard/api.js          fetch + CSRF + error normalising  ~90
public/js/dashboard/shell.js        nav, hash routing, farm/date filters, polling ~230
public/js/dashboard/charts.js       mkChart, sparkline, tooltip, arcGauge (ported) ~200
public/js/dashboard/view_overview.js
public/js/dashboard/view_weather.js
public/js/dashboard/view_compare.js
public/js/dashboard/view_iot.js
public/js/dashboard/view_resources.js
public/js/dashboard/view_map.js
public/js/dashboard/view_now.js
public/js/dashboard/view_control.js
api/overview.py                     new endpoint
```

`public/` is already symlinked to `sites/assets/upande_irrigation`, so modules load from
`/assets/upande_irrigation/...` with no build step. Views are ES modules; MapLibre and Three.js
are dynamically imported inside `view_map.js` so the other seven views don't pay for them.

### View module contract

Every `view_*.js` default-exports:

```js
{
  id: 'weather',
  label: 'Weather',
  icon: '<path …/>',              // inline SVG paths, Feather-style, stroke:currentColor
  mount(el, ctx),                 // render skeleton into el, wire listeners
  refresh(),                      // (re)fetch and paint; called on filter change + poll
  unmount(),                      // clear timers/observers
  pollMs,                         // optional; shell owns the interval
}
```

`ctx` carries `{ api, charts, filters, onFilterChange, csrfToken, user }`. `shell.js` never
reaches into a view's internals; a view never touches the sidebar. Only the active view is
mounted — switching views unmounts the previous one so polls don't stack.

## `api/overview.py`

One whitelisted `fetch(farm=None)` returning tiles + alerts in a single round trip. Each item is
one cheap SQL query against the current week's window (week start from `Irrigation Scheduler.
week_starts_on`, default Thursday, matching `api.scheduler.run`):

**Tiles** — sections irrigating now (reuses the `live_sections` window predicate); total week
deficit (`SUM(total_water_needed_mm)`); delivered vs needed (`SUM(delivered_depth_mm)` /
`SUM(total_water_needed_mm)`); week rainfall vs clean ET crop; planners created this week;
valves on now.

**Alerts** — each with `severity` (`hot`/`warn`/`clay`/`ok`), `title`, `detail`, and an optional
desk `route`:

| Alert | Rule |
|---|---|
| Pump-capped shifts | `capacity_warning LIKE '%Pump capacity capped%'` this week → count + total mm carrying over |
| Chronic deficit | `unmet_deficit_mm > 50` this week (the engine's `CHRONIC_DEFICIT_THRESHOLD_MM`) |
| Persistent deficit | `capacity_warning LIKE '%PERSISTENT DEFICIT%'` |
| Disease risk | latest `Weather Reading.z_value >= 15` per farm |
| Missing readings | `DATEDIFF(today, MAX(date))` per irrigation farm ≥ 2 |
| Stale sensors | `Sensor Readings` devices whose `MAX(timestamp)` is > 6 h old |
| Scheduler health | `Irrigation Scheduler.last_run_status` in (`Partial`,`Failed`) |

Empty alert list renders as a positive "nothing needs attention" state rather than a blank card.
The endpoint degrades per-query: a failing alert query is logged and omitted, never 500s the
whole tile row, because `Sensor Readings` and the meter doctypes live in other apps and may be
absent on some sites.

## Weather Reading entry

A card at the top of the Weather view: farm (defaults to the active filter, or the only
irrigation farm), date (defaults to today, `max=today`), rainfall mm, pan cups, min °C, max °C.

Submits via `frappe.client.insert` to create a normal `Weather Reading`, so
`events.weather_reading.compute_derived` runs untouched — `pan_depth_mm`, `et_pan`, `et_crop`,
`temperature_average`, `effective_temp`, `cumulative_temperature`, `swd`, `z_value`,
`z_risk_level` and every validation (`rainfall >= 0`, max ≥ min, one-per-farm-per-day) come
along for free. No duplicate computation in JavaScript.

Server-side `frappe.throw` messages are surfaced verbatim in the card, since they are already
operator-worded ("A Weather Reading for X on Y already exists. Open the existing record to edit
it instead."). `frappe.msgprint` output — the negative-pan-evaporation anomaly warning — arrives
in `_server_messages` and is rendered as a warning banner rather than being swallowed. On
success the form clears and the Weather view refreshes.

If today's reading already exists, the card renders in a "logged" state showing today's computed
`et_crop` / `swd` / `z_value` with a link to the desk record, instead of an empty form.

## Error handling

- `api.js` normalises Frappe's error envelope: HTTP status, `exc_type`, `_server_messages`, and
  the `exception` field, into `{ message, serverMessages[], status }`.
- Each view owns its own status strip; one failing view never blanks the shell.
- 403 anywhere → redirect to `/login?redirect-to=/upande-irrigation` (the session expired
  behind an open tab, which the current pages handle by showing a raw `HTTP 403`).
- The map degrades exactly as it does today: valves and block polygons fetch in parallel, either
  can fail independently, and an empty result shows a message rather than an ocean view.

## Doctype ownership

`Weather Reading` is already `module: Upande Irrigation` in the site DB but absent from
`fixtures/doctype.json`, so a fresh install has no Weather Reading and `compute_derived`'s hook
points at nothing. Fixed by re-exporting fixtures with `bench export-fixtures`.

The same export fixes the stale `Irrigation Planner` (last exported 2026-05-18, 20 fields)
which is missing 12 fields `compute_shift` writes to and `live_sections` selects: `farm`,
`shift_hours`, `cycles_count`, `cycle_hours_each`, `cycle_plan`, `unmet_deficit_mm`,
`carried_deficit_mm`, `delivered_depth_mm`, `capacity_warning`, `scheduled_start`,
`scheduled_end`, `irrigation_calculations`, `weather_completeness`, `active_shift_count`,
`irrigation_deficit_mm`.

## Verification

- `ruff` + `eslint` via the repo's pre-commit config.
- `bench --site kaitet.local build --app upande_irrigation` and `clear-cache`.
- HTTP check: `/upande-irrigation` returns 200 and the three old routes return 301 to the right
  targets.
- Each of the 8 views renders without console errors against live `kaitet.local` data.
- `api.overview.fetch` returns tiles + alerts; verified by direct call in `bench console`.
- A Weather Reading created through the form has non-null `et_crop`, `swd` and `z_value`,
  proving the hook ran.
