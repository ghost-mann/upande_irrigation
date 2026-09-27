# Upande Irrigation — Daily Water Balance and Run Sheet

**Date:** 2026-09-28
**Status:** Approach A and Section 1 (data model) approved in discussion. The user delegated
the remaining sections: "finish im afk". Sections 2–6 are decided below, from the answers
given in the discussion.

## Why the current planner is replaced

The data migrated from v15 (webstore.localhost, 2026-09-27) shows the weekly planner is not
used and does not produce meaningful numbers:

- 1,626 Irrigation Planners: **0 submitted, 0 marked irrigated, 0 with more than zero required
  hours**. Some weeks have 97–110 planners for 55 shifts (duplicate generation).
- **No soil memory.** Each week is an isolated bucket. `Weather Reading.swd` exists but is
  unbounded (starts at −500 mm and drifts to −583 mm) and nothing reads it.
- **Rain is counted twice and treated as fully effective.** `et_pan = rain + pan` already
  includes the day's rain, then `deficit = clean_et − Σrain` subtracts it again. Any week with
  about 15 mm of rain or more gives zero demand.
- **Coverage is applied backwards.** `hours = deficit ÷ mm_hr ÷ coverage` raises the hours for
  a partly wetted area. There is no application efficiency, and the 2.8 mm/h rate contradicts
  the settings' own 250 trees/ha × 70 L/h (= 1.75 mm/h).
- **Contradictory coefficients.** The engine uses pan coefficient 0.95, while `default_kpan` is
  0.75. Kc is fixed at 0.65 all year. Coverage exists as both 62.5 and 70. Depth per cup is
  stored twice.
- **Inconsistent weather history.** Of 594 Lokitela rows since 2025, 407 store
  `et_pan = rain + pan` and the other 187 (the 2026-04-24 bulk import) store pan depth only.
- **The schedule ignores operations.** Cycles are stacked from Thursday 00:00 with no run
  windows.
- **No feedback.** Delivered water is assumed to equal planned water.

## Decisions from the discussion

1. **Approach A**: a daily per-block soil water balance, a daily Run Sheet for operators, and
   the weekly view, valve state and water budget derived from the same engine.
2. **Users: all of them.** Operators (daily run sheet), the manager (weekly view), valve
   control (planned windows drive the valves' scheduled state), and reporting (water budget).
3. **Per-block data the farm can supply:** soil texture, planting year, emitters per tree and
   L/h, and canopy cover %. Farm defaults are used when a value is blank.
4. **Actuals, strongest source first:** the valve open/close log, then the operator's
   run-sheet ticks. Section water meters are a cross-check, and irrometers correct the modelled
   soil water.
5. **Run windows are configurable per pump**, with rest days.
6. **Shifts are farm-defined** (the existing Irrigation Shift Block mapping) and run one at a
   time per pump. A warning is raised when a shift's emitter flow exceeds its pump's flow.

## 1. Data model (approved)

| DocType | Kind | Purpose |
|---|---|---|
| **Irrigation Block Profile** | new, one per block (name = block Warehouse) | area_ha, tree_count, planting_year, soil_texture, root_depth_m, canopy_cover_pct, emitters_per_tree, emitter_flow_lph, application_efficiency, depletion_fraction (p), is_active. Read-only derived fields: tree_age_years, application_rate_mm_hr, block_flow_m3_hr, taw_mm, raw_mm |
| **Irrigation Settings** | existing single, cleaned | pan coefficient `kpan`, `depth_per_cup_mm`, effective-rain rule, `depletion_fraction` default, `balance_start_date`, `min_run_hours`, `max_run_hours_per_day`, `irrigate_ahead_days`, `irrometer_weight`, `default_*` for every profile field, plus child tables **Irrigation Age Band** (age_from/age_to years, root depth, canopy %, Kc Jan…Dec), **Irrigation Soil Texture** (texture → available water mm per m) and **Irrigation Tension Point** (centibars → depletion fraction) |
| **Irrigation Pump Profile** | existing, extended | `pump_flow_rate_m3_per_hr` (exists); new child table **Irrigation Run Window** (day of week, start, end) and a `rest_dates` note |
| **Irrigation Block Balance** | new, computed, read-only | one row per (block, date): epan_mm, etc_mm, rain_mm, effective_rain_mm, irrigation_mm, irrigation_source (None/Run Sheet/Valve Log), depletion_mm (end of day), taw_mm, raw_mm, irrometer_cb, irrometer_adjusted, weather_estimated. Rebuildable from its sources at any time |
| **Irrigation Run Sheet** | new, one per (farm, date) | generated_at, status (Draft/Issued/Closed), notes. Child **Irrigation Run**: shift, pump, section, cycle_no, cycles, planned_start, planned_end, planned_hours, net_mm, urgency, reason, status (Planned/Running/Done/Partial/Skipped/Not placed), actual_start, actual_end, actual_hours, skip_reason, notes |
| **Valve Event** | new, append-only | valve, block, at, from_state, to_state, source (Operator/Schedule/Hardware), user |
| **Irrigation Planner** | retired | read-only history. Moved to a "History" sidebar section and its before_save hook removed. The 1,626 records are kept |

## 2. Engine (`engine/balance.py`, pure; `engine/runsheet.py`, pure)

Per block, per day, in FAO-56 form:

```
Epan      = rain + pan_cups × depth_per_cup          (from the raw inputs, so the
                                                      187 pan-only rows are right too)
            missing day → mean Epan of the previous 7 recorded days (weather_estimated)
Kc        = age band's Kc for the month
Kr        = GC + 0.5 × (1 − GC)                      GC = canopy cover fraction (Keller)
ETc       = Epan × Kp × Kc × Kr
TAW       = soil available water (mm/m) × root depth (m)
RAW       = p × TAW
eff_rain  = min( max(0, rain − rain_loss_mm) × rain_efficiency , D_prev + ETc )
irr_net   = recorded hours × rate_mm_hr × application_efficiency
D_today   = clamp(D_prev + ETc − eff_rain − irr_net, 0, TAW)
irrometer : if a reading exists, D ← D + w × (f(cb) × TAW − D)   (w = irrometer_weight)
rate      = trees × emitters_per_tree × L/h ÷ (area_ha × 10 000)        mm/h
```

Water beyond field capacity drains away and is not banked (the clamp at 0). The balance
starts at field capacity (D = 0) on `balance_start_date` (default 60 days before the first
run), which is the standard assumption after a wet spell.

Projection: from today's D, step forward with ETc from the mean Epan of the last 7 days and no
rain. The day a block's D reaches RAW is its trigger day, and the depth needed is the D on that
day (refill to field capacity).

A shift's need is the **maximum over its member blocks**. The blocks share valves and open
together, so the driest block sets the run. Gross hours = D ÷ efficiency ÷ rate, bounded to
[min_run_hours, max_run_hours_per_day], and split into cycles with the existing cycle rule
(`auto_cycle_threshold_hrs`, `cycle_rest_hours`).

Defaults are seeded into the settings tables so the engine works before anyone edits them:

- **Soil (mm/m):** sand 60, loamy sand 90, sandy loam 120, loam 160, silt loam 190,
  clay loam 170, clay 150.
- **Age bands:** 0–2 y (root 0.3 m, canopy 20 %, Kc 0.45), 3–4 y (0.45 m, 45 %, 0.60),
  5+ y (0.6 m, 70 %, 0.75, with 0.80 in the dry months Jan–Mar).
- **Tension points:** 0 cb→0, 10→0.1, 30→0.5, 60→0.8, 100→1.0.
- **Other:** Kp 0.75, p 0.5, rain loss 2 mm, rain efficiency 0.9, application efficiency 0.9.

All of these are tunable, and are labelled in the settings form as starting points to be
calibrated against irrometers.

## 3. Run sheet generation (`api/runsheet.py`)

`generate(farm, date)` runs daily from cron at the Scheduler's `run_hour` (default 05:00), or
from a button:

1. Rebuild the balance for every block up to yesterday, and project from today.
2. **Due shifts:** any member block's projected D at the end of today is at or above RAW.
   **Ahead shifts:** they will trigger within `irrigate_ahead_days` (default 1). Ahead shifts
   are only used to fill spare window time, and are labelled "Ahead".
3. Per pump, order by urgency (max D/RAW, then trigger day) and place cycles one at a time in
   that pump's run windows for the date. Cycles of the same shift are separated by
   `cycle_rest_hours`.
   A section with **no Irrigation Pump Profile** uses the settings' default run window
   (06:00–18:00), and all such sections share one queue, because an unknown pump might be one
   pump. The rows say so. A profile whose window table is empty still gets nothing placed.
   *(Decided during implementation: without it a fresh site would place nothing at all.)*
4. Whatever does not fit is written as **Not placed**, with the hours needed. Its blocks'
   deficit keeps growing, so it ranks higher tomorrow. Nothing is silently dropped.
5. It is idempotent: regenerating replaces only rows still **Planned** or **Not placed**. Rows
   Running, Done, Partial or Skipped are the operator's record and are kept.
6. Warnings are recorded on the row's `reason`: a shift flow above the pump's flow, a block
   using defaults because its profile is empty, or weather estimated or stale (no reading in
   3 days).

## 4. Actuals and feedback

- **Valve Event** is written by `set_override`, `set_override_bulk` and (in future) hardware.
  For a block on a day, the open intervals of its valves (Forced Open overrides and Hardware
  "ON") are that block's irrigation hours when any exist (`irrigation_source = Valve Log`).
- Otherwise the run sheet's **Done** rows (planned hours) and **Partial** rows (actual hours)
  count, split per cycle (`irrigation_source = Run Sheet`).
- **Section water meters** are not fed into the balance. The budget compares metered m³ with
  recorded m³ per section and week, and flags a gap above 20 %.
- **Irrometers** correct D on the day they are read (blend by `irrometer_weight`, default 0.5).
- **Valve scheduled state** (`valves.list_states`): a valve is scheduled ON when now falls
  inside a Planned or Running run-sheet cycle for a shift containing its block. This replaces
  the Irrigation Planner windows.

## 5. Screens

- **Dashboard "Irrigation plan"** (replaces the Planning view), with tabs:
  - **Today:** the run sheet as a per-pump timeline and a list. Each row has Start, Done,
    Partial (actual hours) and Skip (reason), plus Regenerate.
  - **Week:** shifts × 7 days, showing trigger days and hours, with pump load per day against
    its window hours.
  - **Blocks:** every block's depletion as a bar against RAW and TAW, days to trigger, last
    irrigated and rate. It is sortable, and clicking a block shows its 30-day balance chart
    with the projection.
  - **Budget:** per section and week, the water needed, recorded and metered, with the gap.
- **Irrigation Now** reads the run sheet.
- **Field Map block card** gains a "Soil water" section: depletion against RAW and TAW, days
  to trigger, last irrigated.
- **Overview alerts** come from the new model: blocks past RAW, Not placed rows, skipped
  cycles, stale weather, and blocks on defaults.
- **Desk:** Irrigation Block Profile, Irrigation Run Sheet, Valve Event and Block Balance are
  in the sidebar and the navigation tiles. Irrigation Planner moves to "History".

## 6. Migration

- **Seed a profile for each block warehouse:** `area_ha` from the block geometry's `area_m2`
  (v15 carried it in `custom_raw_geojson` properties), and `tree_count` from its `tree_count`
  property. Everything else stays blank and uses defaults. The seeding is idempotent and never
  overwrites a value someone entered.
- **Settings:** migrate `default_kpan` to `kpan` (0.75) and merge the duplicate fields.
- **Scheduler:** the weekly Friday cron is removed and a daily cron added. The week-based
  scheduler fields are hidden, and `shift_blocks` stays.
- **Irrigation Planner:** its DocPerms are reduced to read-only.
- **Weather Readings are not rewritten.** The engine derives Epan from the raw inputs, which
  makes both styles of stored `et_pan` irrelevant.

## Testing

- **Pure engine:** each formula, clamping, effective rain capped by depletion, the irrometer
  blend, projection trigger day, the shift max rule, cycle splitting, window placement,
  Not placed overflow, and idempotent regeneration.
- **Integration:** a profile with defaults, the balance rebuild over the migrated Lokitela data
  (no raise, D stays within [0, TAW]), run sheet generation for Lokitela, run-sheet Done
  feeding next day's balance, a Valve Event interval taking priority, and list_states reading
  the run sheet.
- **UI:** headless load of every view without console errors.

## Out of scope

- A forecast ET₀ source (Approach C), to add later as an optional projection input.
- Hardware valve integration beyond the Valve Event log.
- The Water & Energy tabbed redesign (proposed separately, not yet approved).
