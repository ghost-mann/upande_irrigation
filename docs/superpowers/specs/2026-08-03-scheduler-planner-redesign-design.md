# Irrigation Scheduler & Planner — Redesign

**Date:** 2026-08-03
**Status:** Approved in discussion; awaiting spec review

## Why

836 planners across 29 weeks produced **819 with zero irrigation hours (98%)**.
The cause is structural, not sporadic:

```
created 2026-07-10  →  plans 2026-07-16 … 07-22  →  "0 of 7 days logged"
created 2026-07-03  →  plans 2026-07-09 … 07-15  →  "0 of 7 days logged"
created 2026-06-26  →  plans 2026-07-02 … 07-08  →  "0 of 7 days logged"
```

Every planner is generated six days before the week it plans, then aggregates
Weather Readings **inside that future week**. Those readings do not exist yet, so
`weekly_et_crop` is 0, the deficit is 0, and the planner writes *"Rainfall 0 mm
meets or exceeds Clean ET Crop 0 mm. No irrigation required."*

The planner asks how much water the crop lost during a week that has not
happened. Missing data yields a confident instruction not to irrigate — it fails
dangerous. The 72 planners that did produce hours are ones re-saved after their
week elapsed; they show ET 29.95 mm, rain 49.37 mm, 2.07 hr/shift. The
arithmetic is sound. It is pointed at the wrong week.

Supporting findings, all verified against the live site:

| Finding | Evidence |
|---|---|
| No pump capacity data exists at all | `SELECT COUNT(*) FROM tabIrrigation Pump Profile` → **0** |
| 599 planners fell back to 168 hr/wk | `capacity_warning LIKE '%fallback%'` |
| Per-block overrides never affect hours | `compute_shift` computes from farm defaults, reads child rows only to rebuild them |
| Cycles are contiguous | `cycle_plan` splits hours; `assign_daily_blocks` schedules one block; `live_sections` treats cycles as adjacent |
| Every section starts at hour 0 | `_DAILY_ANCHOR_HOUR = 0`, so sections run concurrently while capping is per-section |
| Carry-forward breaks on a gap | lookup requires `to_date = from_date − 1` exactly |

## Decisions taken

1. The 836 planners are backfill, not operational history — regenerate, don't migrate.
2. Demand comes from the **preceding 7 days** (replacement irrigation).
3. Incomplete weather → **refuse to plan and flag**, never a silent zero.
4. Sub-blocks within a shift share one rate — **drop the per-block override fields**.
5. Fix cycle spacing, pump sharing and carry-forward in this pass, alongside the
   demand-window fix.

## What does not change

Every formula is preserved exactly:

```
pan_depth      = pan_cups × depth_per_cup_mm
et_pan         = rainfall + pan_depth
et_crop        = et_pan × pan_to_crop_coefficient
clean_et_crop  = Σ et_crop × et_crop_coefficient        (Kc)
week_deficit   = max(0, clean_et_crop − Σ rainfall)
hours          = deficit ÷ mm_hr ÷ (coverage ÷ 100)
cycles         = ceil(hours ÷ auto_cycle_threshold_hrs), floored at cycles_when_*
z_value        = −58.99 + 3.22·T̄ + 0.18·rain₇d
SWD, GDD       unchanged
```

Only *which period feeds them* and *who decides final hours* changes.

## Architecture — demand and allocation split

`compute_shift` is a `before_save` hook on one planner. Pump sharing, cycle
placement and carry-forward all need to know about other shifts. A
single-document hook cannot answer them.

**Planner = demand.** Pure function of this shift plus the measured week. Writes
the deficit, the hours required before capping, and which week it measured. No
clock, no pump.

**Scheduler = allocation.** Holds every shift for the farm at once. Applies real
pump capacity across sections, decides granted hours, lays shifts across the
seven days with cycle rest gaps, writes `scheduled_start`/`scheduled_end`, and
carries any shortfall.

Consequence, accepted: saving a planner by hand shows demand and required hours
but **not** granted hours or windows. Those come from a scheduler run. A single
shift cannot know the pump is busy.

### Field ownership

| Field | Written by | Meaning |
|---|---|---|
| `measured_from`, `measured_to` *(new)* | planner | which elapsed week demand came from |
| `weather_completeness` | planner | "7 of 7 days logged" |
| `weekly_rainfall_mm`, `weekly_et_pan_mm`, `raw_et_crop_mm`, `clean_et_crop_mm` | planner | measured aggregates |
| `this_week_deficit` | planner | `max(0, clean_et − rain)` |
| `carried_deficit_mm` | planner | balance brought forward |
| `total_water_needed_mm` | planner | deficit + carried |
| `required_hours` *(new)* | planner | hours to meet demand, uncapped |
| `shift_hours` | **allocator** | hours actually granted |
| `delivered_depth_mm` | allocator | `granted × mm_hr × coverage` |
| `unmet_deficit_mm` | allocator | `total − delivered` |
| `cycles_count`, `cycle_hours_each`, `cycle_plan` | allocator | depend on granted hours |
| `scheduled_start`, `scheduled_end` | allocator | the window |
| `capacity_warning` | allocator | why it was capped |

### New settings

On **Irrigation Settings**: `min_weather_days` (Int, default 7) and
`cycle_rest_hours` (Float, default 2.0).

`min_weather_days = 7` is the strictest setting — a single missed reading refuses
the week. That is deliberate for launch, because the failure it replaces was
silent under-watering, and a refusal is visible. If it proves too brittle in
practice the number is one field away from 6 or 5; the extrapolation policy is
not reintroduced without a decision.

## Calculating a section

```
1  MEASURE   preceding 7 days, per farm (one weather station per farm)
             Σ rainfall_mm, Σ et_crop, mean temp
             count readings; if < min_weather_days → REFUSE this farm-week

2  DEMAND    clean_et_crop = Σ et_crop × Kc
             week_deficit  = max(0, clean_et_crop − Σ rainfall)

3  PER SHIFT carried  = unmet from the most recent prior planner for this shift
             total    = week_deficit + carried
             required = total ÷ default_mm_hr ÷ (default_coverage ÷ 100)

4  ALLOCATE  group sections by Irrigation Pump Profile.pump_name
             pump_week_hours = water_target_m3_per_week ÷ pump_flow_rate_m3_per_hr
             if Σ required > pump_week_hours → scale every shift by the same
                 factor (pump_week_hours ÷ Σ required), so a thirsty shift keeps
                 its larger share instead of being levelled to the mean. The old
                 rule divided capacity equally (WEEK_HOURS ÷ N), which penalised
                 shifts with real demand to fund ones with none.
             a section with no pump profile falls back to 168 hr/wk and is
                 marked unverified rather than silently treated as capped
             sequence shifts across 7 days, concurrent draw ≤ pump capacity
             split each shift into cycles separated by cycle_rest_hours

5  SETTLE    delivered = granted × mm_hr × coverage
             unmet     = total − delivered      → carried by the next week
```

### Carry-forward

Replace the exact-date chain with *the most recent prior planner for this shift*:

```sql
WHERE farm = %(farm)s AND block = %(block)s AND to_date < %(from_date)s
ORDER BY to_date DESC LIMIT 1
```

A skipped week no longer drops the debt. No new storage — deliberately not a new
balance doctype, because the planner already records `unmet_deficit_mm` and one
ordered lookup is enough.

### Refusal

Below `min_weather_days` the scheduler creates no planner for that farm-week,
records the reason on Irrigation Scheduler Run, marks the run `Partial`, and the
Overview surfaces it as an alert naming the farm, the week and the count. The
week passing unplanned is the intended outcome — it is visible and actionable,
where a silent zero is neither.

## Doctypes

```
Farm  (upande_kaitet)               is_irrigation_farm
 └─ Warehouse  warehouse_type=Section        custom_farm
     └─ Block Type  "23HA - SHIFT 1"         the SHIFT · is_active, farm
         └─ Blocks List (child)              → Warehouse warehouse_type=Block

Weather Reading         per farm/day   → et_pan, et_crop, SWD, GDD, Z
Irrigation Settings     Single         Kc, rates, cycle thresholds, + new fields
Irrigation Pump Profile per (farm, section): pump_name, flow m³/hr, target m³/wk
Irrigation Scheduler    Single         week start, run day/hour, guards
Irrigation Scheduler Run (+ Run Shift) audit + refusal reasons
Irrigation Planner      submittable    one per shift per week
```

**Removed:** the `irrigation_calculations` child table, the `Irrigation
Calculation` doctype, and the `Irrigation Planner Section` handlers in
`public/js/irrigation_planner.js` that recalculate those rows.

## Visualisation

Reuses the CRM `SERIES` ramp for section identity so the two products agree.
Validated against the irrigation card surface `#fafaf6`:

```
node scripts/validate_palette.js "#3268c4,#c69210,#03958c,#b5501f,#8d4fa0,#5f8d33" \
     --mode light --surface "#fafaf6"

[PASS] Lightness band       all 6 inside L 0.43–0.77
[PASS] Chroma floor         all 6 ≥ 0.1
[PASS] CVD separation       worst adjacent ΔE 13.3 deutan · 12.2 tritan
[PASS] Normal-vision floor  worst adjacent ΔE 19.3
[WARN] Contrast             #c69210 at 2.67:1 → relief required
```

The gold WARN obligates relief, not dismissal: always-on legend values and direct
end-labels, never colour alone.

**Colour jobs.** Sections carry *identity* — the six-hue ramp in fixed order,
bound to the section so 23HA is azure on every chart and filtering never repaints
survivors. Shift state uses the *reserved status* palette (on-track green, capped
amber, deficit red) always beside a label. Pump load uses a *single* gold hue for
magnitude. No chart has two y-axes.

### 1 · Week schedule (centrepiece)

Sections as rows, real time as x, each shift a block at its actual
`scheduled_start`/`end`, cycles as separate segments with rest gaps visibly
empty. Makes the allocator legible: cycling is three runs with rests, not one
long block. Hover gives shift, hours, cycles, and why capped. Replaces the
day-bucket grid, which can only say "how many shifts today".

### 2 · Pump load profile

Step area of concurrent draw (m³/hr) across the week, per pump, with capacity as
a threshold rule; over-subscription reads as area breaching the line. One
measure, one axis. This is what proves the concurrency fix.

### 3 · Section water balance

Required vs delivered as the existing ghost/fill bars with a target tick; carried
debt as a small-multiple line per section in the SERIES ramp. Answers "who is
falling behind, and is it worsening".

### 4 · Derivation panel — not a chart

A labelled step-through on the planner: measured ET → × Kc → − rainfall →
+ carried → ÷ mm/hr → ÷ coverage → required → capped → granted, each with number
and unit. The steps are divisions, not additive contributions, so a waterfall
would misrepresent them. Value is showing the working, so a number can be trusted
or challenged.

### Interaction and accessibility

Crosshair + tooltip on the pump profile; per-mark hover on the Gantt and balance
bars. Legend present for ≥ 2 series, ≤ 4 also direct-labelled. Table view
available for the gold-contrast relief. Filters in one row above the charts.

## Dependencies and risks

- **Zero Irrigation Pump Profile rows exist.** Charts 1–2 and the whole
  allocation step are inert until they are populated. Until then the allocator
  must fall back to the current 168 hr/wk behaviour *and say so*, rather than
  pretending to have capped.
- **One weather station per farm.** ET and rainfall are farm-level; sections in a
  farm share them. Correct for Lokitela; revisit if a farm gets a second station.
- **Regeneration wipes 836 planners.** Agreed as backfill. Only the 72 with 7/7
  coverage carried meaning.
- **Dark mode is out of scope.** It requires its own steps validated against a
  dark surface, not a flip.

## Phasing

Two phases, each independently shippable. The visuals depend on the engine
producing real numbers, so building them first would mean designing against
zeroes.

- **Phase 1 — engine.** Demand window, refusal, the demand/allocation split,
  carry-forward, cycle spacing, pump grouping, removal of the per-block fields,
  regeneration of the backfill.
- **Phase 2 — visualisation.** The four visuals, the palette module, and the
  table views.

## Verification

- A farm-week with < 7 readings produces no planner and a named refusal on the run.
- A farm-week with 7/7 produces non-zero `required_hours` matching a hand check.
- With a pump profile whose capacity is below total demand, granted hours are
  capped, `unmet_deficit_mm` is positive, and the next week's planner carries it.
- A deliberately skipped week still carries its debt into the following plan.
- Concurrent draw in the allocator never exceeds pump capacity.
- Cycles are separated by `cycle_rest_hours` in `scheduled_start`/`end` and in the
  Gantt.
- `node scripts/validate_palette.js` passes for every categorical palette shipped.
- Each of the four visuals screenshotted and eyeballed for collisions and overflow.
