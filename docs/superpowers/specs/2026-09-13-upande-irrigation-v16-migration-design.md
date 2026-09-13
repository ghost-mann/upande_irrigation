# Upande Irrigation — v15 → v16 Migration

**Date:** 2026-09-13
**Status:** Approved in discussion; awaiting spec review

## Why

`upande_irrigation` runs on **kaitet-group.c.frappe.cloud** (Frappe v15) and must
move to **kaitet-group.upande.com** (Frappe 16.32.0 / ERPNext 16.33.0), carrying
all live data. The destination does not have the app:

```
upande.com installed apps (38):  builder, crm, erpnext, frappe, hrms, lending,
  upande_accounting, upande_agriculture, upande_core, upande_crm, …
  ↑ no upande_irrigation, and no "Upande Irrigation" Module Def or DocTypes
```

The move is not a redeploy. Three structural problems have to be solved first,
all verified against both live sites.

### 1. The schema lives in the database, not in the app

The app source defines **two** DocType folders (`irrometer_reading`,
`tank_and_valve`). The live site has **ten**. The other eight exist only as rows,
shipped by a fixture hook:

```python
fixtures = [{"doctype": "DocType", "filters": [["module", "=", "Upande Irrigation"]]}, …]
```

| DocType | `custom` | `istable` | `issingle` |
|---|---|---|---|
| Irrigation Planner | 1 | 0 | 0 |
| Irrigation Scheduler | 1 | 0 | 1 |
| Irrigation Scheduler Run | 1 | 0 | 0 |
| Irrigation Scheduler Run Shift | 1 | 1 | 0 |
| Irrigation Settings | 1 | 0 | 1 |
| Irrometer Reading | 1 | 1 | 0 |
| Reservoir Pumping Record | 1 | 0 | 0 |
| Water Transfer | 1 | 0 | 0 |
| Weather Reading | 1 | 0 | 0 |
| Tank And Valve | **0** | 0 | 0 |

Nine of ten are `custom: 1`. `bench migrate` does not manage them, the schema is
not in git, and every deployment is a fixture import. Two further inconsistencies:
`Irrometer Reading` is `istable: 1` live but has a standalone doctype folder in
code, and `Tank And Valve` is already `custom: 0`.

### 2. `Irrigation Planner.block` points at a doctype that does not exist on v16

```
Irrigation Planner (1,346 records)
  └─ .block  [Link, reqd=1]  →  Block Type   (57 records, module "Custom" — no app owns it)
                                  ├─ .block_type → Block configaration  (module Upande Kaitet)
                                  └─ .blocks     → Blocks List          (child table)
```

`upande_kaitet` is not installed on upande.com, and neither `Block Type` nor
`Block configaration` exists there. Every planner's required link is dangling at
the destination.

`Block Type` does not model a block. It models an **irrigation shift** — a group
of 1–3 block-warehouses watered together. Its `Blocks List` child rows already
store destination warehouse names verbatim:

```
70HA - SHIFT 20  →  BONDENI BLK 7 - KL, BONDENI BLK 8 - KL   (section 70HA_SECTION - KL)
56HA - SHIFT 1   →  MIMA BLK 9 - KL                          (section 56HA_SECTION - KL)
```

**All 89 child rows across all 57 shifts resolve to real Warehouses on
upande.com. Zero missing.** Blocks are already the warehouse list on both sides.
`Block Type` is a wrapper around data the destination already holds.

The app's own engine agrees: `Irrigation Scheduler Run Shift` stores
`section` and `block` as Data-with-Warehouse-options, and `Tank And Valve.block`
and `Irrometer Reading.irrigation_block` are Links to Warehouse. Only
`Irrigation Planner.block` is still tied to `Block Type`.

### 3. `Irrigation Pump Profile` is a second orphaned dependency

`engine/allocate.py`, `api/resources.py` and `events/irrigation_planner.py` all
reference **`Irrigation Pump Profile`**, which lives in module **`Upande Kaitet`** —
an app that is *not* installed on upande.com. It links `irrigation_section` →
Warehouse and carries pump flow rate plus weekly water and energy budgets.

`frappe.db.count` and `frappe.get_all` against a non-existent DocType raise, so
this is a hard break at the destination, not a soft degradation.

It holds **0 records**. There is no data question — it only has to exist.

### 4. Defects in the shift mapping

Verified across all 89 rows:

| Defect | Detail |
|---|---|
| Rows pointing at a **group** warehouse | `23HA - SHIFT 1` (×2 — a straight duplicate), `23HA - SHIFT 2` → `23HA_SECTION - KL`. SHIFT 1 therefore has no real block at all. |
| Identical shift pairs | `70HA` 1≡2 (KINYORO BLK 3+4), 10≡11 (WESA BLK 10+11), 19≡20 (BONDENI BLK 7+8) |
| Blocks in two shifts | 13 blocks. Most plausibly deliberate; `DAIRY BLK 7 - KL` crossing from a 56HA shift into `65HA - SHIFT 7` looks like an error. |
| Section-group blocks in no shift | `BLOCK BLK 1`, `BLOCK BLK 2`, `BLOCK BLK 8`, `DAIRY BLK 1` |
| Inactive shifts | `65HA - SHIFT 2`, `70HA - SHIFT 4` |

Stated section never disagreed with the warehouse's real `parent_warehouse` —
**zero mismatches in 89 rows**. Section is fully derivable and need not be stored.

## Decisions taken

1. The 9 `custom: 1` DocTypes become **real code DocTypes** owned by the app,
   with their fixture Custom Fields folded into the DocType JSON.
2. `Block Type`, `Block configaration` and `Blocks List` are **dropped**;
   `Irrigation Pump Profile` is **adopted** into `upande_irrigation` as a code
   DocType (0 records, so schema only).
3. The shift grouping moves into **one child table on the existing
   `Irrigation Scheduler` Single** — a net reduction of two doctypes.
4. **Warehouse is not extended.** It is referenced only as the block link target.
5. `Irrigation Planner.block` becomes **Data**, keeping its values byte-for-byte.
   All 1,346 planners migrate unchanged; the grain stays one planner per shift.
6. The 89 mapping rows load **verbatim**. Defects are reported, not silently
   repaired — data hygiene is a separate decision after the migration.
7. **Bench dry-run on `kaitet.local` proves the whole path before the cutover.**

## Data to move

From c.frappe.cloud, ~7,500 records:

| DocType | Records |
|---|---|
| Weather Reading | 5,431 |
| Irrigation Planner | 1,346 |
| Reservoir Pumping Record | 369 |
| Water Transfer | 279 |
| Tank And Valve | 58 |
| Irrigation Scheduler Run | 32 |
| Irrometer Reading, Irrigation Scheduler Run Shift | child tables — move with parents |
| Irrigation Scheduler, Irrigation Settings | Singles |
| (shift mapping, from Block Type) | 57 shifts / 89 rows |

Destination dependencies all confirmed present: `Farm` (14, module Upande Core),
`Warehouse` (601), `User` (460).

**The code is ahead of the data.** Local `main` is 20 commits ahead of
`upstream/main` — the whole scheduler/planner redesign (pure demand and allocate
modules, pump capacity, per-shift overrides, the Planning view, the test suite).
None of it is deployed to c.frappe.cloud, and the six patches have not run there.
So the 1,346 planners being migrated were produced by the *old* engine and carry
none of the fields the current code writes. Phase 1 must run the patches and
confirm the new engine reads old records without crashing, before regeneration is
even considered.

## Design

### Shift model

New child table `Irrigation Shift Block`, on `Irrigation Scheduler`. One row per
**(shift, block)** pair — 89 rows for 57 shifts:

| Field | Type | From | Note |
|---|---|---|---|
| `shift` | Data | `Block Type.name` | e.g. `70HA - SHIFT 20` |
| `block` | **Link → Warehouse** | `Blocks List.block` | the only Warehouse reference |
| `farm` | Link → Farm | `Block Type.farm` | shift-level |
| `is_active` | Check | `Block Type.is_active` | shift-level |
| `application_rate_mm_hr` | Float | `Block Type` custom field | shift-level; blank inherits farm default |
| `irrigation_coverage` | Float | `Block Type` custom field | shift-level; blank inherits farm default |

The last four are attributes of the *shift*, so they repeat across a shift's block
rows. That denormalisation is deliberate: the alternative is a second child
doctype for a header, and at 57 shifts the repetition is cheap. **The loader must
validate that every row of a shift agrees on all four**, and fail the load if not.

`application_rate_mm_hr` and `irrigation_coverage` are not optional. They are read
by `shift_settings()` in `events/irrigation_planner.py`, and commit `22fc836` added
them because *"every shift computed identical hours because rate and coverage were
farm-wide only."* Dropping them silently reintroduces that bug.

`block` is a real Link, not the soft Data-with-options the older
`Irrigation Scheduler Run Shift` uses, so validation can reject `is_group`
warehouses — which is precisely what the three bad `23HA` rows are. They fail the
load loudly rather than migrating silently broken.

`section` is **not stored** — derived from the block's `parent_warehouse`, backed
by the zero-mismatch finding above. The `*_SECTION - KL` group warehouses stay as
they are in the existing tree, untouched.

### DocType conversion

For each of the 9, write
`upande_irrigation/upande_irrigation/doctype/<name>/<name>.json` with
`custom: 0`, plus `__init__.py` and a controller. Remove the
`{"doctype": "DocType", …}` entry from `fixtures` in `hooks.py`.

**The DocType fixture is not the whole schema.** `upande_irrigation/fixtures/`
holds four files, and the patches add fields as **Custom Field** records, then tag
them `module = "Upande Irrigation"` by raw SQL so the export picks them up:

| Fixture file | Content |
|---|---|
| `doctype.json` | the 10 DocTypes |
| `custom_field.json` | 23 fields on Irrigation Planner, 10 on Irrigation Settings, 5 on Block Type |
| `property_setter.json` | Irrigation Pump Profile defaults |
| `custom_html_block.json` | Smart Irrigation Dashboard |

So the real runtime schema is `doctype.json` **+** `custom_field.json`. Converting
means folding each DocType's Custom Fields into its DocType JSON — `required_hours`,
`applied_rate_mm_hr`, `applied_coverage_pct`, `measured_from`/`measured_to`,
`cycle_plan` and the rest are all Custom Fields today, not DocFields.

A **`pre_model_sync` patch** flips `custom` to 0 on the existing DocType rows
before `bench migrate` syncs the code definition onto the *same table* — schema
and data preserved, no drop/recreate. Needed on `kaitet.local` (which already has
them as custom); **not** needed on upande.com, which is a clean install.

Reconcile `Irrometer Reading` (`istable` disagreement) and `Tank And Valve`
(already `custom: 0`) before anything else moves.

### v16 compatibility

33 raw `frappe.db.sql` calls to audit: `api/overview.py` (10), `api/sensors.py`
(7), `api/scheduler.py` (5), `api/planner.py` (3), `api/valves.py` (2),
`events/weather_reading.py` (1), and 5 across four of the six patch files. No
`count()`/`as` inside `get_list(fields=…)`, so the v16 SQL-function regression
does not apply. The app has no cross-app Python imports — it imports only
`frappe` and its own modules.

Bench is 16.27.0, destination 16.32.0 — a minor skew to keep in mind when a
behaviour differs between dry-run and cutover.

### UI surfaces

Modeled on `upande_crm`:

- `add_to_apps_screen` in `hooks.py` — title "Upande Irrigation", routing to the
  sidebar, with a `has_permission` guard.
- `upande_irrigation/workspace_sidebar/upande_irrigation.json` — a v16
  `Workspace Sidebar`, `header_icon: "droplet"`, section breaks for **Planning**
  (Irrigation Planner, Scheduler Run), **Operations** (Water Transfer, Reservoir
  Pumping Record, Tank And Valve), **Monitoring** (Weather Reading, Irrometer),
  **Settings** (Irrigation Settings, Irrigation Scheduler); the existing
  dashboard pinned at the bottom as a URL link.
- Icon: `upande-logo.png` **copied** into `upande_irrigation/public/images/`
  rather than referenced at `/assets/upande_core/`, matching the reasoning in the
  CRM's own hooks comment and avoiding a hard dependency on `upande_core`.
- The existing `smart_irrigation` workspace becomes the Overview target.

## Phases

**Phase 1 — bench dry-run on `kaitet.local`** (v16.27.0; the app and
`upande_core`, `upande_crm`, `upande_agriculture`, `erpnext` are installed)

1. Convert the 9 DocTypes to code; add the `pre_model_sync` patch.
2. Add `Irrigation Shift Block`; repoint `Irrigation Planner.block` to Data;
   drop the three legacy doctypes.
3. v16 audit of the raw SQL and the API modules. TDD throughout.
4. Pull all ~7,500 records plus the 57-shift mapping from c.frappe.cloud, staged
   as JSON, inserted preserving `name`, `creation`, `owner`. Child tables move
   with their parents.
5. Build the apps-screen tile, sidebar and icon.
6. Verify: run the weekly scheduler and planner engine at real volume, confirm
   the dashboard renders, confirm every `block` Link resolves.

**Phase 2 — cutover to upande.com.** Same path against the clean destination
(no `custom`-flip patch needed). Gated on Phase 1 passing.

**Phase 3 — data hygiene.** The defect list from *Why §3*, as separate decisions.

## Out of scope

Site-to-site communication between c.frappe.cloud and upande.com. It was raised
in the same conversation but never scoped — what flows, which direction, whether
it is a permanent link or a cutover bridge are all open. It needs its own design.

## Open questions

None blocking. The defect rulings in Phase 3 are deferred by decision (6), not
unresolved.
