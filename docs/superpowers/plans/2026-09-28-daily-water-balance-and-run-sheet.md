# Daily Water Balance and Run Sheet — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the weekly Irrigation Planner with a per-block daily soil water balance that
drives a daily per-farm Run Sheet, valve schedule state, a weekly projection and a water budget.

**Architecture:** There are two pure modules. `engine/balance.py` holds the formulas, the daily
step and the projection. `engine/runsheet.py` works out shift need, cycles and window placement.
Frappe adapters sit around them: `api/balance.py` gathers inputs, rebuilds the Block Balance rows
and records actuals; `api/runsheet.py` generates, updates status and exposes the dashboard data.
The existing dashboard views are rewired to use these.

**Tech Stack:** Frappe 16, Python, vanilla ES modules (the dashboard's shell, charts and maplib).

**Spec:** `docs/superpowers/specs/2026-09-28-daily-water-balance-and-run-sheet-design.md`

## Global Constraints

- Site: webstore.localhost holds the full migrated v15 data. Run tests with `bench --site webstore.localhost run-tests --app upande_irrigation --module <m>`.
- Commits and pushes go to the public ghost-mann/upande_irrigation `main`, with **no Claude co-author lines and no "Claude" in docs**.
- Tests that create records must not leak: use `NoCommit` from `tests/helpers.py` when the code commits.
- Setup must live in `after_migrate` (setup.py), not patches (`bench install-app` skips patches).
- Maps follow the maplib/Esri rules. Chart colours use the dashboard tokens.
- Valve state vocabulary: `Auto`, `Forced Open`, `Forced Closed`.

## Review Focus

1. A block with no profile values: defaults must apply everywhere, and a block with zero area or zero trees must give rate 0, produce no hours, and carry a "no rate" reason instead of dividing by zero.
2. A farm with no weather for more than 3 days: estimated Epan is flagged and the run sheet row states that weather is stale. There must be no crash.
3. Regenerating a run sheet after the operator has marked rows Done or Skipped must keep those rows exactly as they are.
4. A day covered by both a Valve Event interval and a run-sheet Done row must count the water once, preferring the valve log.
5. A pump with no run windows means no placement (every row "Not placed: no run window"), not an unlimited 24-hour pump.

---

### Task 1: Pure balance engine
**Files:** create `engine/balance.py` and `tests/test_balance_engine.py`.
**Produces:**
- `epan(rain, cups, depth_per_cup)`
- `kc_for(age, month, bands)` and `band_for(age, bands)`
- `kr(canopy_pct)`
- `taw(awc_mm_per_m, root_m)`
- `rate_mm_hr(trees, emitters, lph, area_ha)`
- `effective_rain(rain, loss, eff, room)`
- `step(prev_d, etc, rain_eff, irr_net, taw)`
- `tension_fraction(cb, points)` and `blend(d, cb, taw, points, w)`
- `simulate(days, profile, params)`, which returns a list of day dicts
- `project(d0, mean_etc, raw, horizon)`, which returns `{trigger_day, depletion_by_day}`

- [ ] Write the tests: each formula with hand-computed values; clamp to [0, TAW]; rain capped by room; irrometer blend; trigger day; zero rate.
- [ ] Implement, then run the tests until they pass, then commit.

### Task 2: DocTypes and settings
**Files:** new doctypes `irrigation_block_profile`, `irrigation_block_balance`, `irrigation_run_sheet`, `irrigation_run` (child), `valve_event`, `irrigation_age_band`, `irrigation_soil_texture`, `irrigation_tension_point` and `irrigation_run_window` (children). Modify the `irrigation_settings` JSON (new fields plus the three tables), the `irrigation_pump_profile` JSON (run_windows table) and the Irrigation Planner JSON perms (read-only). Controller for the Block Profile computes the derived fields through `engine.balance`. Settings seeding goes in `setup.py`: `ensure_agronomy_defaults()` (tables, when empty) and `seed_block_profiles()` (from block geometry `area_m2`/`tree_count`, never overwriting).
- [ ] Tests (`tests/test_block_profile.py`): derived fields with defaults, the zero-area guard, seeding idempotent, and a profile value that is never overwritten.
- [ ] Migrate, run the tests, commit.

### Task 3: Balance adapter
**Files:** `api/balance.py` and `tests/test_balance_api.py`.
**Produces:**
- `profile_params(block)`, which merges the profile with defaults;
- `farm_weather(farm, start, end)`;
- `irrigation_by_block_day(blocks, start, end)` (valve log first, then run sheet);
- `rebuild(farm=None, start=None, end=None)`, which upserts Block Balance rows;
- whitelisted `block_status(farm=None)`, giving the current D/RAW/TAW, days to trigger and last irrigated per block;
- whitelisted `block_series(block, days=30)`.
- [ ] Tests: rebuild over the Lokitela data keeps D within [0, TAW] and doesn't raise; a Done run row lowers the next day's D; a valve interval wins over the run sheet on the same day.
- [ ] Implement, test, commit.

### Task 4: Run sheet engine and API
**Files:** `engine/runsheet.py`, `api/runsheet.py`, `tests/test_runsheet_engine.py` and `tests/test_runsheet_api.py`.
**Produces:**
- `shift_need(members, params)` → `{hours, net_mm, urgency, trigger_day, reasons}`
- `cycles(hours, settings)`
- `place(requests_by_pump, windows_by_pump, date, rest_h)` → rows with status Planned or Not placed
- whitelisted `generate(farm, date=None)`, which is idempotent
- whitelisted `set_status(row, status, actual_hours=None, skip_reason=None)`
- whitelisted `today(farm=None)` and `week(farm=None)`
- [ ] Tests: urgency order, overflow Not placed, no windows means no placement, regenerate keeps Done/Skipped, and the Ahead fill only uses spare time.
- [ ] Implement, test, commit.

### Task 5: Valves, cron and legacy retirement
**Files:**
- `api/valves.py`: list_states reads run-sheet windows; set_override and set_override_bulk write a Valve Event.
- `hooks.py`: daily cron `upande_irrigation.scheduled.daily.run`; remove the weekly cron and the Irrigation Planner before_save hook.
- `scheduled/daily.py`
- `api/overview.py`: alerts from the new model.
- Sidebar and navigation block: new doctypes, and Planner moved under History.
- [ ] Tests: list_states ON inside a planned cycle; an override writes a Valve Event; the cron entry exists; the planner hook is gone. Update the older tests that asserted weekly behaviour.
- [ ] Implement, test, commit.

### Task 6: Dashboard
**Files:**
- `view_planner.js`, rewritten as "Irrigation plan" with the Today, Week, Blocks and Budget tabs;
- `view_now.js`, reading `runsheet.today`;
- the Field Map block card's "Soil water" section, using `balance.block_status`;
- CSS.
- [ ] Headless check that every view loads with no console errors, plus screenshots of the Today, Week and Blocks tabs.
- [ ] Commit.

### Task 7: Run it on webstore.localhost and push
- [ ] Migrate, seed, rebuild the Lokitela balance and generate today's run sheet; sanity-check the numbers (rates and trigger days plausible); run the full suite against the baseline; push.
