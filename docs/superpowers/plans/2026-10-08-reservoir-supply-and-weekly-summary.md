# Reservoir Supply and Weekly Summary — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record each pump's water source. Warn when the run sheet would draw more from the
reservoir than the river intake refills, reconcile what goes into the reservoir against what
comes out, and show the weekly water balance per section as a Budget table and as a Script
Report.

**Architecture:** One pure function, `engine/runsheet.reservoir_check`, does the supply
arithmetic. `api/runsheet.py` gathers flows, sources and the intake's refill, writes them on
the sheet, and gets the new `reservoir_budget`. A new `api/weekly.py` builds the weekly rows
that both the dashboard and the report read.

**Tech Stack:** Frappe 16, Python, vanilla ES modules.

**Spec:** `docs/superpowers/specs/2026-10-08-reservoir-supply-and-weekly-summary-design.md`

## Global Constraints

- Site: run tests with `bench --site webstore.localhost run-tests --app upande_irrigation --module <m>`.
- Commits go to `main`, with no Claude co-author lines and no "Claude" in docs.
- Tests that hit code which commits use `NoCommit` from `tests/helpers.py`.
- Seeding belongs in `after_migrate`. Patches are only for one-off data fixes (`bench install-app` skips patches).
- A Pump Profile with a blank `pump_role` is a Section pump. A blank `water_source`, or a section with no Pump Profile, counts as Reservoir.

## Review Focus

1. A Pump Profile saved before this change has a NULL `pump_role` and `water_source`. It must still act as a reservoir-fed Section pump. Test: `pumps()` with NULL fields (Task 2).
2. A farm with no intake profile must generate a sheet without raising, with `reservoir_refill_m3` empty and no warning. Test (Task 3).
3. Rows the operator skipped, or that were Not placed, draw no water. Test (Task 1).
4. Blocks with zero area must not cause a divide-by-zero in the weekly summary. They fall back to an unweighted mean. Test (Task 5).
5. When a run sheet is regenerated after the operator has ticked rows, the draw must include the kept Done rows (their actual hours). Test (Task 1, `actual_hours` preferred).

---

### Task 1: Pure reservoir check

**Files:** modify `engine/runsheet.py`. Test: `tests/test_runsheet_engine.py`.

**Produces:**
- `reservoir_check(rows, flows, sources, refill_m3) -> {"draw_m3": float, "refill_m3": float|None, "warning": str, "note": str}`
- `window_hours(windows) -> float`

- [ ] Write the failing tests:

```python
class TestReservoirCheck(FrappeTestCase):
	FLOWS = {"S1": 50, "S2": 40, "R1": 80}
	SOURCES = {"sec-a": "Reservoir", "sec-r": "River direct"}

	def row(self, shift, section, hours, status="Planned", actual=0):
		return {"shift": shift, "section": section, "planned_hours": hours, "status": status, "actual_hours": actual}

	def test_under_refill_no_warning(self):
		out = R.reservoir_check([self.row("S1", "sec-a", 4)], self.FLOWS, self.SOURCES, 1000)
		self.assertEqual(out["draw_m3"], 200)
		self.assertEqual(out["warning"], "")

	def test_over_refill_warns(self):
		out = R.reservoir_check([self.row("S1", "sec-a", 4), self.row("S2", "sec-a", 5)], self.FLOWS, self.SOURCES, 300)
		self.assertEqual(out["draw_m3"], 400)
		self.assertIn("exceeds", out["warning"])

	def test_river_direct_is_not_drawn_from_the_reservoir(self):
		out = R.reservoir_check([self.row("R1", "sec-r", 10)], self.FLOWS, self.SOURCES, 10)
		self.assertEqual(out["draw_m3"], 0)

	def test_unknown_section_counts_as_reservoir(self):
		self.assertEqual(R.reservoir_check([self.row("S1", "sec-x", 2)], self.FLOWS, {}, 1000)["draw_m3"], 100)

	def test_skipped_and_not_placed_draw_nothing(self):
		rows = [self.row("S1", "sec-a", 4, "Skipped"), self.row("S2", "sec-a", 4, "Not placed")]
		self.assertEqual(R.reservoir_check(rows, self.FLOWS, self.SOURCES, 1000)["draw_m3"], 0)

	def test_actual_hours_preferred_for_done_and_partial(self):
		rows = [self.row("S1", "sec-a", 4, "Done", actual=2), self.row("S2", "sec-a", 4, "Partial", actual=1)]
		self.assertEqual(R.reservoir_check(rows, self.FLOWS, self.SOURCES, 1000)["draw_m3"], 140)

	def test_no_intake_is_not_checked(self):
		out = R.reservoir_check([self.row("S1", "sec-a", 40)], self.FLOWS, self.SOURCES, None)
		self.assertIsNone(out["refill_m3"])
		self.assertEqual(out["warning"], "")
		self.assertIn("not checked", out["note"])

	def test_window_hours(self):
		self.assertEqual(R.window_hours([(at(6), at(10)), (at(12), at(14))]), 6.0)
```

- [ ] Run `--module upande_irrigation.tests.test_runsheet_engine`. It should fail with AttributeError.
- [ ] Implement:

```python
COUNTED = ("Planned", "Running", "Done", "Partial")

def window_hours(windows):
	return round(sum((b - a).total_seconds() for a, b in windows or []) / 3600.0, 2)

def reservoir_check(rows, flows, sources, refill_m3):
	"""The day's draw on the reservoir against what the river intake can refill.
	Only cycles that run (or ran) count; a River-direct section never touches it."""
	draw = 0.0
	for r in rows:
		if r.get("status") not in COUNTED:
			continue
		if (sources.get(r.get("section")) or "Reservoir") != "Reservoir":
			continue
		actual = float(r.get("actual_hours") or 0)
		hours = actual if r.get("status") in ("Done", "Partial") and actual else float(r.get("planned_hours") or 0)
		draw += hours * float(flows.get(r.get("shift")) or 0)
	draw = round(draw, 1)
	if refill_m3 is None:
		return {"draw_m3": draw, "refill_m3": None, "warning": "", "note": "no Reservoir intake profile — supply not checked"}
	refill = round(float(refill_m3), 1)
	warning = f"reservoir draw {draw:,.0f} m³ exceeds the river intake's {refill:,.0f} m³ today" if draw > refill else ""
	return {"draw_m3": draw, "refill_m3": refill, "warning": warning, "note": ""}
```

- [ ] Run again. It should pass. Commit: `feat(engine): reservoir draw against the river intake's refill`.

### Task 2: Water source on the Pump Profile, farm on Reservoir Pumping Record

**Files:** modify the `irrigation_pump_profile.json` and `.py`, `reservoir_pumping_record.json`,
`irrigation_run_sheet.json`, `api/runsheet.py` (`pumps`, new `intake`), `patches.txt`, and
create `patches/v1_0/add_reservoir_farm.py`. Test: `tests/test_pump_profile.py`.

**Produces:**
- `pumps(farm)` as before, plus a `source` key, with intake profiles excluded.
- `intake(farm) -> {"pump", "flow", "windows", "rest_dates"} | None`

- [ ] Write the failing tests, in a new `TestWaterSource(NoCommit, FrappeTestCase)` in `test_pump_profile.py`, with a farm and a section warehouse:
  - A section pump without `irrigation_section` raises `frappe.ValidationError`.
  - A second Reservoir intake on the same farm raises `frappe.ValidationError`.
  - `RS.pumps(farm)` excludes the intake, and each section pump has `source`.
  - A profile whose `pump_role` and `water_source` were set to NULL via `frappe.db.set_value` is still returned by `pumps()` with `source == "Reservoir"`.
  - `RS.intake(farm)` returns the intake's flow and windows.
- [ ] Add the JSON fields:
  - Pump Profile: `pump_role` Select (default `Section pump`, before `irrigation_section`), and `water_source` Select (default `Reservoir`, `depends_on: eval:doc.pump_role!='Reservoir intake'`). On `irrigation_section`, change `reqd` to 0 and add `mandatory_depends_on: eval:doc.pump_role!='Reservoir intake'`.
  - Reservoir Pumping Record: `farm` Link Farm, placed after `date`.
  - Run Sheet: `reservoir_draw_m3` Float, `reservoir_refill_m3` Float and `supply_warning` Small Text, all read-only, in a "Reservoir" section.
- [ ] Add the controller `validate`: a section pump must have a section, and only one intake is allowed per farm (`frappe.db.exists` with `name != self.name`).
- [ ] Change `pumps()` to skip `pump_role == "Reservoir intake"` and add `"source": p.water_source or "Reservoir"`. Add `intake()`.
- [ ] Add the patch: when `frappe.db.count("Farm") == 1`, set `farm` on Reservoir Pumping Records where it is NULL or "".
- [ ] Run `bench --site webstore.localhost migrate`, then the tests. They should pass. Commit: `feat: pump role and water source; farm on Reservoir Pumping Record`.

### Task 3: Run sheet writes the supply check, and the Overview alerts on it

**Files:** modify `api/runsheet.py` (`shift_flows`, `generate`, `today`), `api/overview.py`,
and `public/js/dashboard/view_planner.js` (the Today card). Test: `tests/test_runsheet_api.py`.

- [ ] Write the failing tests in `TestRunSheet`:
  - After `generate`, `reservoir_refill_m3` is None and `supply_warning` is empty when no intake exists.
  - With an intake of flow 1 m³/h and window 06:00–07:00, `generate` sets `reservoir_refill_m3 == 1` and `supply_warning` contains "exceeds". The intake is deleted again in a `finally` block.
- [ ] Implement `shift_flows(st, members, pump_of) -> {shift: m³/h}`. This is Σ block_flow_m3_hr, capped at the pump's flow when the pump has one. In `generate`, after the rows are appended:

```python
check = R.reservoir_check([r.as_dict() for r in sheet.runs], shift_flows(st, members, pump_of),
                          {s: p["source"] for s, p in pump_of.items()}, refill)
sheet.reservoir_draw_m3, sheet.reservoir_refill_m3 = check["draw_m3"], check["refill_m3"]
sheet.supply_warning = check["warning"] or check["note"]
```

  Here `refill = ip["flow"] * R.window_hours(R.windows_for_date(date, ip["windows"], ip["rest_dates"]))` when `ip := intake(f)`, and None otherwise. Because a Float column is NOT NULL, a missing refill is stored as 0. Its meaning is carried by the note in `supply_warning`, and the test asserts `not reservoir_refill_m3`.
- [ ] `today()` adds `supply_warning`, `reservoir_draw_m3` and `reservoir_refill_m3` to each sheet. The Overview gets a `supply()` alert of severity "warn" for sheets whose warning contains "exceeds". The Today card shows the warning line.
- [ ] Run the runsheet API tests and the API smoke tests. Commit: `feat(runsheet): reservoir draw vs intake refill, with an Overview alert`.

### Task 4: Reservoir reconciliation

**Files:** modify `api/runsheet.py` (add `reservoir_budget`) and `view_planner.js` (Budget). Test: `tests/test_runsheet_api.py`.

- [ ] Write the failing test. Use one submitted Reservoir Pumping Record (farm, date DAY, 1000 m³) and Water Meter Readings of 600 m³ on the reservoir-fed section and 300 m³ on a River-direct section. `reservoir_budget(FARM, weeks=…)` must return a row for DAY's week with intake 1000, drawn 600 and loss 400. Use `frappe.utils.getdate` week math, and freeze "today" with `patch("frappe.utils.getdate")` only if needed. Simpler: the function takes `start`, and the test passes `start=DAY`'s Monday.
- [ ] Implement `reservoir_budget(farm=None, weeks=8, start=None)`. It makes two grouped SQL queries, maps the meter's section to its farm via `Warehouse.custom_farm`, and excludes sections whose Pump Profile is River direct.
- [ ] Add a Budget tab "Reservoir" table: intake, drawn by the reservoir sections, and loss (m³ and %, flagged when |loss| > 20 %).
- [ ] Commit: `feat(budget): reservoir intake against the sections' meters`.

### Task 5: Weekly summary — API, Budget table, Script Report

**Files:** create `api/weekly.py` and `upande_irrigation/report/weekly_irrigation_summary/{__init__.py, .json, .py, .js}`. Modify `view_planner.js`. Test: `tests/test_weekly_summary.py`.

**Produces:** `summary(farm=None, section=None, from_date=None, to_date=None) -> {"from", "to", "rows": [...]}`, with the row keys as in the spec's table D, plus `section`, `farm` and `week`.

- [ ] Write the failing tests. Insert Irrigation Block Balance rows directly for two blocks in one section, with areas 1 and 3 ha, over 7 days of one week. Block A has etc 2 mm/day and block B has 4 mm/day, so Σ etc is 14 and 28, and the weighted value is (14×1 + 28×3)/4 = 24.5. Add rain and effective rain, and depletion on the last day of 10 and 20 (weighted 17.5). Add a Run Sheet with a Done row of 3 h (actual 2) for the section, and a Planned row of 4 h. Assert etc_mm 24.5, end_depletion_mm 17.5, hours_planned 7 (Done 3 + Planned 4) and hours_applied 2. Add a second test where both areas are 0 and the result is the unweighted mean (21). The report's `execute({"farm": FARM, ...})` must return columns and the same rows.
- [ ] Implement the weekly summary in Python over one balance query and one run query, bucketing by `date - weekday`.
- [ ] Write the report JSON (Script Report, ref doctype Irrigation Block Balance, roles System Manager, Agriculture Manager and Irrigation User). Write `execute(filters)`, and the JS filters (farm Link, section Link Warehouse, from_date and to_date Date).
- [ ] Add a Budget tab "Weekly water balance" table, read from `upande_irrigation.api.weekly.summary`.
- [ ] Run the tests, migrate, and the smoke tests. Commit: `feat: weekly water balance per section — Budget table and Script Report`.

### Task 6: Verify

- [ ] Run the full module suite: `bench --site webstore.localhost run-tests --app upande_irrigation`.
- [ ] Load the dashboard Budget and Today tabs headlessly, or with the existing UI smoke test, and check for console errors.
- [ ] Review the whole branch.
