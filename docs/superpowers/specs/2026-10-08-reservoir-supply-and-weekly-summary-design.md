# Upande Irrigation — Reservoir Supply and Weekly Summary

**Date:** 2026-10-08
**Status:** Sections A–D were presented in discussion. The user chose to keep the daily
balance and run sheet (option 1), "accounting plus a supply warning" for the reservoir, and
"both" for the weekly figures, then delegated the rest ("finish im afk").

## Context

The user brought the old weekly-planner method: Σ ET crop × 0.65 − Σ rain = deficit, and
hours = deficit ÷ mm/h with coverage. That method was retired on 2026-09-28 (see
`2026-09-28-daily-water-balance-and-run-sheet-design.md`) because it counted rain twice and
had no soil memory. The daily balance stays. This spec fills the gaps the user's notes
exposed:

1. **Hydraulics.** The 300 ha avocado farm has four irrigation sections. A river pump fills a
   reservoir, and three section pumps (each with its own water meter) draw from that
   reservoir. The fourth section's pump and meter sit at the river, so that section never
   touches the reservoir. The app has no record of which section draws from where, and
   nothing checks the reservoir.
2. **The weekly figures in the farm's terms.** These are Σ ET crop, Σ rain, deficit and hours
   per section per week. The daily balance computes them, but no screen or report shows them
   together.

## A. Water source (data)

**Irrigation Pump Profile** gets two new fields:

- `pump_role` Select `Section pump` / `Reservoir intake`, default `Section pump`.
- `water_source` Select `Reservoir` / `River direct`, default `Reservoir`. It only shows when
  the role is Section pump.

`irrigation_section` becomes `mandatory_depends_on` Section pump (today it is `reqd`). The
controller enforces two rules: a Section pump needs a section, and a farm has at most one
Reservoir intake. A Reservoir intake keeps the existing flow (`pump_flow_rate_m3_per_hr`) and
run windows. Its refill capacity for a date is flow × the hours of its windows on that date.

**Reservoir Pumping Record** gets a `farm` Link field (optional). Patch
`add_reservoir_farm` fills it in on existing records when the site has exactly one Farm.

`api/runsheet.pumps(farm)` returns section pumps only, so an intake never becomes a cycle
queue. A section with no Pump Profile counts as reservoir-fed.

## B. Reservoir supply warning (run sheet)

`engine/runsheet.reservoir_check(rows, flows, sources, refill_m3)` is a pure function:

- `rows`: the sheet's cycles. Each one counts when its status is Planned, Running, Done or
  Partial. Its hours are the actual hours for Done or Partial when those are set, and the
  planned hours otherwise.
- `flows`: {shift: m³/h}. This is the shift's emitter flow (Σ block_flow_m3_hr of its member
  blocks), capped at its pump's flow when the pump's flow is known.
- `sources`: {section: "Reservoir" | "River direct"}. A section missing from it counts as
  Reservoir.
- `refill_m3`: the intake's capacity for the day, or `None` when the farm has no intake
  profile.

It returns `{draw_m3, refill_m3, warning}`. When there is an intake and draw > refill, the
warning reads: "reservoir draw X m³ exceeds the river intake's Y m³ today". With no intake,
the warning is empty and a note reads "no Reservoir intake profile — supply not checked".

Irrigation Run Sheet gets three read-only fields: `reservoir_draw_m3`, `reservoir_refill_m3`
and `supply_warning`. `generate()` fills them after placement. Cycles are never removed or
moved because of the check. The Overview gets a "warn" alert when today's sheet has a
`supply_warning`.

## C. Reservoir reconciliation (Budget)

`api/runsheet.reservoir_budget(farm, weeks)` returns one row per farm per week (Monday
start), each with:

- `intake_m3` = Σ `volume_of_water_used_m3` of submitted Reservoir Pumping Records
- `drawn_m3` = Σ submitted Water Meter Reading `units_used` for that farm's reservoir-fed
  sections (`water_source` = Reservoir, or no Pump Profile)
- `loss_m3` = intake − drawn, and `loss_pct` = loss ÷ intake (when intake > 0)

Records with a blank farm are grouped under farm `None` ("Unassigned"). The Budget tab shows
the result as a "Reservoir" table under the existing water budget, and flags |loss| > 20 %.

## D. Weekly summary

`api/weekly.summary(farm=None, section=None, from_date=None, to_date=None)` returns one row
per section per week (Monday start). The default range is the last 8 weeks. The data comes
from Irrigation Block Balance joined to Irrigation Block Profile (for area) and Warehouse
(for parent section). Every mm figure is the **area-weighted mean over the section's blocks**:
Σ(mm × area_ha) ÷ Σ area_ha.

| Column | Meaning |
|---|---|
| days | days with a balance row in that week |
| epan_mm | Σ pan evaporation |
| etc_mm | Σ crop water use (ET crop) |
| rain_mm | Σ rain |
| effective_rain_mm | Σ rain that reached the root zone |
| deficit_mm | etc_mm − effective_rain_mm. The week's net demand, in the farm's own terms; can be negative |
| irrigation_mm | Σ net irrigation recorded |
| end_depletion_mm | depletion on the week's last day, carrying soil memory forward |
| hours_planned | Σ planned hours of Planned/Running/Done/Partial run-sheet cycles for the section's shifts |
| hours_applied | Σ hours of Done (planned hours, or actual if set) and Partial (actual) cycles |

Hours come from Irrigation Run rows whose `section` is that section, inside the week.

The Budget tab shows the result as a "Weekly water balance" table. The Script Report
**Weekly Irrigation Summary** (module Upande Irrigation, ref doctype Irrigation Block Balance)
has filters for farm, section, from_date and to_date, and calls the same function.

## Testing

- Pure tests for `reservoir_check`: draw under refill, draw over refill, a River-direct
  section excluded, Skipped and Not placed rows excluded, actual hours preferred, and no
  intake.
- Pump Profile validation: a section pump without a section is rejected, and a second intake
  on the same farm is rejected.
- `pumps()` excludes the intake. `generate()` fills the three sheet fields, and a tiny intake
  sets the warning.
- `summary`: area weighting over two blocks of different area, week bucketing, and hours from
  run-sheet rows.
- `reservoir_budget`: a reservoir-fed section counted, a River-direct section excluded.
- The report's `execute()` returns columns and rows.

## Out of scope

- Reservoir capacity and level tracking. A hard limit on supply would need both, and the farm
  records neither.
- Changing the agronomic method. The flat 0.65 utilisation and the weekly deficit stay
  retired, and the weekly deficit is shown for reading only.
