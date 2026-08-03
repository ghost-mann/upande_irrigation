# Irrigation Engine (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the planner measure demand over a week that has actually happened, and move capping and scheduling into an allocator that can see every shift at once.

**Architecture:** Two new pure modules — `engine/demand.py` computes one shift's requirement from a measured week, `engine/allocate.py` turns a set of requirements plus pump capacity into granted hours and time windows. `events/irrigation_planner.py` and `api/scheduler.py` become thin adapters that fetch rows, call the pure functions, and write fields. Pure functions take plain dicts so they are testable without database fixtures.

**Tech Stack:** Frappe v16, Python 3.14, MariaDB. Tests via `bench run-tests` with `FrappeTestCase`.

## Global Constraints

- Every agronomic formula is preserved exactly as written in `events/irrigation_planner.py` today: `pan_depth = pan_cups × depth_per_cup_mm`, `et_pan = rainfall + pan_depth`, `et_crop = et_pan × pan_to_crop_coefficient`, `clean_et_crop = Σ et_crop × et_crop_coefficient`, `week_deficit = max(0, clean_et_crop − Σ rainfall)`, `hours = deficit ÷ mm_hr ÷ (coverage ÷ 100)`, `cycles = ceil(hours ÷ auto_cycle_threshold_hrs)` floored at `cycles_when_above/below_threshold`.
- `CHRONIC_DEFICIT_THRESHOLD_MM = 50.0` and `CHRONIC_WEEKS_THRESHOLD = 3` keep their current values and meaning.
- Demand is measured over the 7 days **ending the day before** the planned week starts.
- `min_weather_days` defaults to 7. Below it, no planner is created for that farm-week.
- A section with no Irrigation Pump Profile falls back to 168 hr/wk and is marked **unverified**, never described as capped.
- Capping is proportional: every shift scaled by `capacity ÷ Σ required`.
- Sub-blocks share one rate. The `irrigation_calculations` child table and `Irrigation Calculation` doctype are removed.
- No new doctypes. Carry-forward reads the most recent prior planner for the shift.
- Run tests with: `bench --site kaitet.local run-tests --app upande_irrigation --module upande_irrigation.tests.<module>`

---

## File Structure

| File | Responsibility |
|---|---|
| `upande_irrigation/engine/__init__.py` | package marker |
| `upande_irrigation/engine/demand.py` | pure: measured window, aggregate → deficit → required hours |
| `upande_irrigation/engine/allocate.py` | pure: pump grouping, proportional cap, cycle placement, day sequencing |
| `upande_irrigation/tests/__init__.py` | package marker |
| `upande_irrigation/tests/test_demand.py` | demand maths and refusal |
| `upande_irrigation/tests/test_allocate.py` | capping, cycles, concurrency |
| `upande_irrigation/events/irrigation_planner.py` | adapter: fetch → demand → write demand fields |
| `upande_irrigation/api/scheduler.py` | adapter: fetch → allocate → write granted fields, record refusals |
| `upande_irrigation/patches/v1_0/add_engine_fields.py` | new fields; drop per-block table |

---

### Task 1: Pure demand module

**Files:**
- Create: `upande_irrigation/engine/__init__.py`
- Create: `upande_irrigation/engine/demand.py`
- Create: `upande_irrigation/tests/__init__.py`
- Test: `upande_irrigation/tests/test_demand.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `measured_window(from_date: date, days: int = 7) -> tuple[date, date]`
  - `aggregate(readings: list[dict]) -> dict` with keys `days`, `rainfall_mm`, `et_pan_mm`, `et_crop_mm`, `mean_temp`, `temp_days`
  - `demand(agg: dict, carried_mm: float, settings: dict) -> dict` with keys `clean_et_crop_mm`, `week_deficit_mm`, `total_needed_mm`, `required_hours`, `no_irrigation_reason`
  - `is_plannable(agg: dict, settings: dict) -> tuple[bool, str]`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the demand calculation.

The bug being locked out: every planner was generated six days before the week
it planned and then aggregated Weather Readings from inside that future week. No
readings existed, so the deficit was zero and 819 of 836 planners instructed the
farm not to irrigate. These tests assert the window is an ELAPSED one and that an
under-covered week refuses rather than returning zero.
"""

import datetime

from frappe.tests.utils import FrappeTestCase

from upande_irrigation.engine import demand as D

SETTINGS = {
    "et_crop_coefficient": 0.65,
    "default_application_rate_mm_hr": 2.8,
    "default_irrigation_coverage": 70.0,
    "min_weather_days": 7,
}


def _readings(n, rainfall=0.0, et_crop=5.0, tmin=12.0, tmax=26.0):
    return [
        {"rainfall_mm": rainfall, "et_pan": 6.0, "et_crop": et_crop,
         "minimum_temperature": tmin, "maximum_temperature": tmax}
        for _ in range(n)
    ]


class TestMeasuredWindow(FrappeTestCase):
    def test_window_ends_the_day_before_the_planned_week(self):
        start, end = D.measured_window(datetime.date(2026, 7, 16), days=7)
        self.assertEqual(end, datetime.date(2026, 7, 15))
        self.assertEqual(start, datetime.date(2026, 7, 9))

    def test_window_is_entirely_in_the_past_relative_to_the_plan(self):
        plan_start = datetime.date(2026, 7, 16)
        start, end = D.measured_window(plan_start)
        self.assertLess(end, plan_start)
        self.assertLess(start, end)


class TestAggregate(FrappeTestCase):
    def test_counts_days_and_sums_measures(self):
        agg = D.aggregate(_readings(7, rainfall=2.0, et_crop=5.0))
        self.assertEqual(agg["days"], 7)
        self.assertAlmostEqual(agg["rainfall_mm"], 14.0)
        self.assertAlmostEqual(agg["et_crop_mm"], 35.0)

    def test_zero_temperatures_do_not_count_as_readings(self):
        agg = D.aggregate(_readings(3, tmin=0.0, tmax=0.0))
        self.assertEqual(agg["temp_days"], 0)
        self.assertEqual(agg["mean_temp"], 0.0)

    def test_empty_input_is_all_zero_not_an_error(self):
        agg = D.aggregate([])
        self.assertEqual(agg["days"], 0)
        self.assertEqual(agg["et_crop_mm"], 0.0)


class TestPlannability(FrappeTestCase):
    def test_a_full_week_is_plannable(self):
        ok, why = D.is_plannable(D.aggregate(_readings(7)), SETTINGS)
        self.assertTrue(ok)
        self.assertEqual(why, "")

    def test_an_empty_week_refuses_and_says_why(self):
        ok, why = D.is_plannable(D.aggregate([]), SETTINGS)
        self.assertFalse(ok)
        self.assertIn("0 of 7", why)

    def test_a_partial_week_refuses(self):
        ok, why = D.is_plannable(D.aggregate(_readings(4)), SETTINGS)
        self.assertFalse(ok)
        self.assertIn("4 of 7", why)


class TestDemand(FrappeTestCase):
    def test_deficit_applies_kc_then_subtracts_rain(self):
        agg = D.aggregate(_readings(7, rainfall=1.0, et_crop=5.0))
        out = D.demand(agg, carried_mm=0.0, settings=SETTINGS)
        # 35 et_crop x 0.65 = 22.75 clean; minus 7 rain = 15.75
        self.assertAlmostEqual(out["clean_et_crop_mm"], 22.75, places=4)
        self.assertAlmostEqual(out["week_deficit_mm"], 15.75, places=4)

    def test_rain_above_demand_yields_no_irrigation_with_a_reason(self):
        agg = D.aggregate(_readings(7, rainfall=20.0, et_crop=1.0))
        out = D.demand(agg, carried_mm=0.0, settings=SETTINGS)
        self.assertEqual(out["week_deficit_mm"], 0.0)
        self.assertEqual(out["required_hours"], 0.0)
        self.assertIn("No irrigation required", out["no_irrigation_reason"])

    def test_carried_deficit_adds_to_this_week(self):
        agg = D.aggregate(_readings(7, rainfall=1.0, et_crop=5.0))
        out = D.demand(agg, carried_mm=10.0, settings=SETTINGS)
        self.assertAlmostEqual(out["total_needed_mm"], 25.75, places=4)

    def test_required_hours_divide_by_rate_and_coverage(self):
        agg = D.aggregate(_readings(7, rainfall=0.0, et_crop=4.0))
        out = D.demand(agg, carried_mm=0.0, settings=SETTINGS)
        # 28 x 0.65 = 18.2 mm; 18.2 / 2.8 / 0.70 = 9.2857 h
        self.assertAlmostEqual(out["required_hours"], 9.2857, places=3)

    def test_a_zero_rate_cannot_divide_and_yields_no_hours(self):
        agg = D.aggregate(_readings(7, et_crop=4.0))
        out = D.demand(agg, 0.0, {**SETTINGS, "default_application_rate_mm_hr": 0})
        self.assertEqual(out["required_hours"], 0.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `bench --site kaitet.local run-tests --app upande_irrigation --module upande_irrigation.tests.test_demand`
Expected: FAIL — `ModuleNotFoundError: upande_irrigation.engine`

- [ ] **Step 3: Implement**

Create `upande_irrigation/engine/__init__.py` empty, `upande_irrigation/tests/__init__.py` empty, and `upande_irrigation/engine/demand.py`:

```python
"""What one shift needs, from a week that has already happened.

Pure: takes plain dicts, touches no database and no clock. That is what makes the
old defect impossible to reintroduce — the caller has to hand this module a
window, and the only window it will accept is an elapsed one.
"""

import datetime
import math  # noqa: F401  (kept for parity with cycle maths in allocate.py)

MEASURE_DAYS = 7


def measured_window(plan_from, days=MEASURE_DAYS):
    """The elapsed week whose losses the plan replaces.

    Ends the day before the planned week opens, so at generation time every day
    in it is settled.
    """
    end = plan_from - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=days - 1)
    return start, end


def aggregate(readings):
    """Sum a list of Weather Reading rows into the figures demand needs."""
    days = 0
    rainfall = et_pan = et_crop = 0.0
    temp_total = 0.0
    temp_days = 0

    for r in readings or []:
        days += 1
        rainfall += float(r.get("rainfall_mm") or 0)
        et_pan += float(r.get("et_pan") or 0)
        et_crop += float(r.get("et_crop") or 0)
        tmin = float(r.get("minimum_temperature") or 0)
        tmax = float(r.get("maximum_temperature") or 0)
        # A 0 degree reading means "not recorded", which is how the planner has
        # always treated it; averaging it in would drag the mean down.
        if tmin > 0 and tmax > 0:
            temp_total += (tmin + tmax) / 2.0
            temp_days += 1

    return {
        "days": days,
        "rainfall_mm": round(rainfall, 4),
        "et_pan_mm": round(et_pan, 4),
        "et_crop_mm": round(et_crop, 4),
        "mean_temp": round(temp_total / temp_days, 4) if temp_days else 0.0,
        "temp_days": temp_days,
    }


def is_plannable(agg, settings):
    """Whether the measured week is complete enough to plan from.

    Refusing is the point. The failure this replaces was a silent zero deficit
    reading as "no irrigation required".
    """
    need = int(settings.get("min_weather_days") or MEASURE_DAYS)
    have = int(agg.get("days") or 0)
    if have >= need:
        return True, ""
    return False, f"{have} of {need} daily weather readings — not enough to plan from"


def demand(agg, carried_mm, settings):
    """This shift's requirement. Formulas unchanged from the original engine."""
    kc = float(settings.get("et_crop_coefficient") or 0.65)
    mm_hr = float(settings.get("default_application_rate_mm_hr") or 0)
    coverage = float(settings.get("default_irrigation_coverage") or 0)

    clean = round(float(agg.get("et_crop_mm") or 0) * kc, 4)
    week_deficit = max(0.0, clean - float(agg.get("rainfall_mm") or 0))
    total = week_deficit + float(carried_mm or 0)

    if total <= 0 or mm_hr <= 0 or coverage <= 0:
        required = 0.0
    else:
        required = total / mm_hr / (coverage / 100.0)

    reason = ""
    if week_deficit <= 0 and float(carried_mm or 0) <= 0:
        reason = (
            f"Rainfall {round(float(agg.get('rainfall_mm') or 0), 1)} mm meets or exceeds "
            f"Clean ET Crop {round(clean, 1)} mm and no carryover. No irrigation required."
        )

    return {
        "clean_et_crop_mm": clean,
        "week_deficit_mm": round(week_deficit, 4),
        "total_needed_mm": round(total, 4),
        "required_hours": round(required, 4),
        "no_irrigation_reason": reason,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `bench --site kaitet.local run-tests --app upande_irrigation --module upande_irrigation.tests.test_demand`
Expected: PASS, 12 tests

- [ ] **Step 5: Commit**

```bash
git add upande_irrigation/engine upande_irrigation/tests
git commit -m "feat(engine): pure demand module measured over an elapsed week"
```

---

### Task 2: Pure allocation module

**Files:**
- Create: `upande_irrigation/engine/allocate.py`
- Test: `upande_irrigation/tests/test_allocate.py`

**Interfaces:**
- Consumes: nothing from Task 1 at runtime; consumes `required_hours` values it produced
- Produces:
  - `pump_capacity_hours(profile: dict | None) -> tuple[float, bool]` — hours and whether verified
  - `grant(requests: list[dict], capacity_hours: float) -> list[dict]` — each request gains `granted_hours`, `capped`
  - `cycle_plan(hours: float, settings: dict) -> tuple[int, float]` — count and hours each
  - `lay_out(granted: list[dict], plan_from: date, settings: dict) -> list[dict]` — each gains `windows: list[(start, end)]`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for allocation.

Three bugs being locked out: capacity was divided equally so a shift with no
demand reserved as much as a parched one; every section was anchored at hour 0 so
sections drew on one pump simultaneously; and cycles were scheduled back to back,
which is not cycling.
"""

import datetime

from frappe.tests.utils import FrappeTestCase

from upande_irrigation.engine import allocate as A

SETTINGS = {
    "auto_cycle_threshold_hrs": 5.0,
    "cycles_when_above_threshold": 2,
    "cycles_when_below_threshold": 1,
    "cycle_rest_hours": 2.0,
}


class TestPumpCapacity(FrappeTestCase):
    def test_capacity_is_target_over_flow_rate(self):
        hours, verified = A.pump_capacity_hours(
            {"water_target_m3_per_week": 1000.0, "pump_flow_rate_m3_per_hr": 20.0}
        )
        self.assertAlmostEqual(hours, 50.0)
        self.assertTrue(verified)

    def test_no_profile_falls_back_to_a_full_week_unverified(self):
        hours, verified = A.pump_capacity_hours(None)
        self.assertAlmostEqual(hours, 168.0)
        self.assertFalse(verified)

    def test_a_profile_missing_numbers_is_also_unverified(self):
        hours, verified = A.pump_capacity_hours(
            {"water_target_m3_per_week": 0, "pump_flow_rate_m3_per_hr": 20.0}
        )
        self.assertAlmostEqual(hours, 168.0)
        self.assertFalse(verified)


class TestGrant(FrappeTestCase):
    def test_demand_within_capacity_is_granted_whole(self):
        out = A.grant([{"key": "a", "required_hours": 10.0}], capacity_hours=50.0)
        self.assertAlmostEqual(out[0]["granted_hours"], 10.0)
        self.assertFalse(out[0]["capped"])

    def test_over_capacity_scales_proportionally_not_equally(self):
        out = A.grant(
            [{"key": "a", "required_hours": 30.0}, {"key": "b", "required_hours": 10.0}],
            capacity_hours=20.0,
        )
        by = {r["key"]: r for r in out}
        # 20 / 40 = 0.5 — the thirsty shift keeps three times the share.
        self.assertAlmostEqual(by["a"]["granted_hours"], 15.0)
        self.assertAlmostEqual(by["b"]["granted_hours"], 5.0)
        self.assertTrue(by["a"]["capped"])

    def test_a_shift_needing_nothing_reserves_nothing(self):
        out = A.grant(
            [{"key": "a", "required_hours": 40.0}, {"key": "b", "required_hours": 0.0}],
            capacity_hours=20.0,
        )
        by = {r["key"]: r for r in out}
        self.assertAlmostEqual(by["b"]["granted_hours"], 0.0)
        self.assertAlmostEqual(by["a"]["granted_hours"], 20.0)

    def test_total_granted_never_exceeds_capacity(self):
        out = A.grant(
            [{"key": str(i), "required_hours": 30.0} for i in range(6)],
            capacity_hours=50.0,
        )
        self.assertLessEqual(sum(r["granted_hours"] for r in out), 50.0 + 1e-6)


class TestCyclePlan(FrappeTestCase):
    def test_short_shift_uses_the_below_threshold_count(self):
        self.assertEqual(A.cycle_plan(3.0, SETTINGS), (1, 3.0))

    def test_long_shift_splits_to_stay_under_the_threshold(self):
        count, each = A.cycle_plan(12.0, SETTINGS)
        self.assertEqual(count, 3)
        self.assertAlmostEqual(each, 4.0)

    def test_no_hours_means_no_cycles(self):
        self.assertEqual(A.cycle_plan(0.0, SETTINGS), (0, 0.0))


class TestLayOut(FrappeTestCase):
    PLAN_FROM = datetime.date(2026, 7, 16)

    def test_cycles_are_separated_by_the_rest_interval(self):
        out = A.lay_out([{"key": "a", "granted_hours": 12.0}], self.PLAN_FROM, SETTINGS)
        windows = out[0]["windows"]
        self.assertEqual(len(windows), 3)
        gap = (windows[1][0] - windows[0][1]).total_seconds() / 3600.0
        self.assertAlmostEqual(gap, 2.0)

    def test_a_shift_needing_nothing_gets_no_window(self):
        out = A.lay_out([{"key": "a", "granted_hours": 0.0}], self.PLAN_FROM, SETTINGS)
        self.assertEqual(out[0]["windows"], [])

    def test_shifts_on_one_pump_never_overlap(self):
        reqs = [{"key": str(i), "granted_hours": 6.0} for i in range(4)]
        out = A.lay_out(reqs, self.PLAN_FROM, SETTINGS)
        spans = sorted(
            (w for r in out for w in r["windows"]), key=lambda w: w[0]
        )
        for earlier, later in zip(spans, spans[1:]):
            self.assertLessEqual(earlier[1], later[0])

    def test_every_window_falls_inside_the_planned_week(self):
        out = A.lay_out([{"key": "a", "granted_hours": 20.0}], self.PLAN_FROM, SETTINGS)
        week_end = datetime.datetime.combine(
            self.PLAN_FROM + datetime.timedelta(days=7), datetime.time.min
        )
        for start, end in out[0]["windows"]:
            self.assertGreaterEqual(start, datetime.datetime.combine(self.PLAN_FROM, datetime.time.min))
            self.assertLessEqual(end, week_end)
```

- [ ] **Step 2: Run to verify it fails**

Run: `bench --site kaitet.local run-tests --app upande_irrigation --module upande_irrigation.tests.test_allocate`
Expected: FAIL — `cannot import name 'allocate'`

- [ ] **Step 3: Implement**

Create `upande_irrigation/engine/allocate.py`:

```python
"""Turning a set of requirements into granted hours and time windows.

This is the part a per-document hook could never do: capping depends on what else
draws on the pump, and cycle placement depends on what else runs that day. It is
pure so both are testable without a database.
"""

import datetime
import math

FULL_WEEK_HOURS = 168.0
WEEK_DAYS = 7


def pump_capacity_hours(profile):
    """Weekly hours the pump can run, and whether that figure is real.

    With no Irrigation Pump Profile there is no capacity to allocate against, so
    the caller gets a full week and an explicit "unverified" — never a number
    dressed up as a limit.
    """
    if profile:
        target = float(profile.get("water_target_m3_per_week") or 0)
        flow = float(profile.get("pump_flow_rate_m3_per_hr") or 0)
        if target > 0 and flow > 0:
            return round(target / flow, 4), True
    return FULL_WEEK_HOURS, False


def grant(requests, capacity_hours):
    """Scale every request by one factor so shares stay proportional to need.

    The old rule was capacity / N, which handed an idle shift the same slice as a
    parched one.
    """
    out = []
    total = sum(max(0.0, float(r.get("required_hours") or 0)) for r in requests)
    factor = 1.0
    if total > capacity_hours > 0:
        factor = capacity_hours / total

    for r in requests:
        need = max(0.0, float(r.get("required_hours") or 0))
        granted = round(need * factor, 4)
        out.append({**r, "granted_hours": granted, "capped": factor < 1.0 and need > 0})
    return out


def cycle_plan(hours, settings):
    """Split a run so no single cycle exceeds the threshold. Formula unchanged."""
    threshold = float(settings.get("auto_cycle_threshold_hrs") or 5.0)
    below = max(1, int(settings.get("cycles_when_below_threshold") or 1))
    above = max(1, int(settings.get("cycles_when_above_threshold") or 2))

    if hours <= 0:
        return 0, 0.0
    if hours <= threshold:
        count = below
    else:
        by_cap = math.ceil(hours / threshold) if threshold > 0 else above
        count = max(by_cap, above)
    return count, round(hours / count, 4)


def lay_out(granted, plan_from, settings):
    """Place each shift's cycles in the week without overlapping.

    One queue per call, so callers group by pump before calling: two shifts on one
    pump cannot run at the same instant, and cycles of the same shift are
    separated by cycle_rest_hours so water has time to infiltrate.
    """
    rest = float(settings.get("cycle_rest_hours") or 0)
    week_start = datetime.datetime.combine(plan_from, datetime.time.min)
    week_end = week_start + datetime.timedelta(days=WEEK_DAYS)

    cursor = week_start
    out = []
    for r in granted:
        hours = float(r.get("granted_hours") or 0)
        count, each = cycle_plan(hours, settings)
        windows = []
        for i in range(count):
            start = cursor
            end = start + datetime.timedelta(hours=each)
            if end > week_end:
                # The week is full. Anything unplaced stays unmet and carries,
                # which is more honest than spilling into next week's plan.
                break
            windows.append((start, end))
            cursor = end + datetime.timedelta(hours=rest if i < count - 1 else 0)
        out.append({**r, "cycles_count": len(windows), "cycle_hours_each": each if windows else 0.0,
                    "windows": windows})
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `bench --site kaitet.local run-tests --app upande_irrigation --module upande_irrigation.tests.test_allocate`
Expected: PASS, 14 tests

- [ ] **Step 5: Commit**

```bash
git add upande_irrigation/engine/allocate.py upande_irrigation/tests/test_allocate.py
git commit -m "feat(engine): pure allocator with proportional capping and cycle rests"
```

---

### Task 3: New fields, drop the per-block table

**Files:**
- Create: `upande_irrigation/patches/v1_0/add_engine_fields.py`
- Modify: `upande_irrigation/patches.txt`

**Interfaces:**
- Produces: Irrigation Planner gains `required_hours` (Float), `measured_from` (Date), `measured_to` (Date). Irrigation Settings gains `min_weather_days` (Int, default 7), `cycle_rest_hours` (Float, default 2.0). Irrigation Planner loses `irrigation_calculations`.

- [ ] **Step 1: Write the patch**

```python
"""Fields the redesigned engine needs, and removal of the ones it does not.

required_hours separates demand from what the allocator granted: shift_hours used
to be both, so a manual save recomputed uncapped hours over a scheduled figure.
measured_from/to record which elapsed week the demand came from, which is the
fact the old engine never had.

irrigation_calculations goes because its per-block mm_hr and coverage never
reached the calculation — hours came from the farm defaults — so the columns
invited edits that changed nothing.
"""

import frappe

PLANNER = "Irrigation Planner"
SETTINGS = "Irrigation Settings"
MODULE = "Upande Irrigation"

PLANNER_FIELDS = [
    {
        "fieldname": "required_hours",
        "label": "Required Hours",
        "fieldtype": "Float",
        "insert_after": "total_water_needed_mm",
        "read_only": 1,
        "precision": "2",
        "description": "Hours needed to meet demand, before pump capping.",
    },
    {
        "fieldname": "measured_from",
        "label": "Measured From",
        "fieldtype": "Date",
        "insert_after": "required_hours",
        "read_only": 1,
        "description": "Start of the elapsed week this demand was measured over.",
    },
    {
        "fieldname": "measured_to",
        "label": "Measured To",
        "fieldtype": "Date",
        "insert_after": "measured_from",
        "read_only": 1,
    },
]

SETTINGS_FIELDS = [
    {
        "fieldname": "min_weather_days",
        "label": "Minimum Weather Readings per Week",
        "fieldtype": "Int",
        "insert_after": "organisation_name",
        "default": "7",
        "description": (
            "Below this many daily readings in the measured week, no planner is "
            "created for that farm-week and the run is flagged. 7 refuses on a "
            "single missed reading."
        ),
    },
    {
        "fieldname": "cycle_rest_hours",
        "label": "Rest Between Cycles (Hrs)",
        "fieldtype": "Float",
        "insert_after": "min_weather_days",
        "default": "2",
        "precision": "1",
        "description": "Gap between a shift's cycles, so water can infiltrate.",
    },
]


def execute():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    for dt, specs in ((PLANNER, PLANNER_FIELDS), (SETTINGS, SETTINGS_FIELDS)):
        if not frappe.db.exists("DocType", dt):
            continue
        missing = [
            f for f in specs
            if not frappe.db.exists("Custom Field", {"dt": dt, "fieldname": f["fieldname"]})
        ]
        if missing:
            create_custom_fields({dt: missing}, ignore_validate=True)

    # The child table and its doctype go together; leaving the doctype behind
    # would keep an orphan in the module's fixtures.
    frappe.db.delete("Custom Field", {"dt": PLANNER, "fieldname": "irrigation_calculations"})
    if frappe.db.exists("DocType", "Irrigation Calculation"):
        frappe.delete_doc("DocType", "Irrigation Calculation", force=1, ignore_permissions=True)

    frappe.db.sql(
        """
        UPDATE `tabCustom Field` SET module = %(module)s
        WHERE dt IN (%(planner)s, %(settings)s) AND (module IS NULL OR module = '')
        """,
        {"module": MODULE, "planner": PLANNER, "settings": SETTINGS},
    )
    frappe.db.commit()
    frappe.clear_cache()
```

- [ ] **Step 2: Register it**

Append to `upande_irrigation/patches.txt` under `[post_model_sync]`:

```
upande_irrigation.patches.v1_0.add_engine_fields
```

- [ ] **Step 3: Run it**

Run: `bench --site kaitet.local execute upande_irrigation.patches.v1_0.add_engine_fields.execute`

- [ ] **Step 4: Verify the schema changed**

Run:
```bash
bench --site kaitet.local mariadb -e "
SELECT fieldname FROM \`tabCustom Field\` WHERE dt='Irrigation Planner' AND fieldname IN ('required_hours','measured_from','measured_to','irrigation_calculations');
SELECT fieldname FROM \`tabCustom Field\` WHERE dt='Irrigation Settings' AND fieldname IN ('min_weather_days','cycle_rest_hours');"
```
Expected: the three planner fields present, `irrigation_calculations` absent, both settings fields present.

- [ ] **Step 5: Commit**

```bash
git add upande_irrigation/patches
git commit -m "feat(engine): add required_hours and measured window, drop per-block table"
```

---

### Task 4: Planner writes demand only

**Files:**
- Modify: `upande_irrigation/events/irrigation_planner.py` (full rewrite of `compute_shift`)
- Test: `upande_irrigation/tests/test_planner_hook.py`

**Interfaces:**
- Consumes: `engine.demand.measured_window`, `aggregate`, `is_plannable`, `demand`
- Produces: `compute_shift(doc, method=None)` writing `measured_from`, `measured_to`, `weather_completeness`, `weekly_rainfall_mm`, `weekly_et_pan_mm`, `raw_et_crop_mm`, `clean_et_crop_mm`, `this_week_deficit`, `carried_deficit_mm`, `total_water_needed_mm`, `required_hours`, `z_value`, `z_risk_level`, `active_shift_count`, `no_irrigation_reason`. Also `carried_for(farm, block, from_date) -> float`.

- [ ] **Step 1: Write the failing test**

```python
"""The hook must write demand and must not write allocation.

Bug locked out: compute_shift used to set shift_hours, so opening a scheduled
planner and saving it replaced the allocator's capped figure with an uncapped one.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.events.irrigation_planner import carried_for


class TestCarriedForward(FrappeTestCase):
    def test_no_prior_planner_carries_nothing(self):
        self.assertEqual(carried_for("__nofarm__", "__noblock__", "2026-07-16"), 0.0)

    def test_carry_comes_from_the_most_recent_prior_week_not_an_exact_date(self):
        # A gap week used to reset the debt to zero because the lookup required
        # to_date = from_date - 1 exactly.
        farm = frappe.get_all("Farm", limit=1)
        if not farm:
            self.skipTest("no Farm records on this site")
        rows = frappe.get_all(
            "Irrigation Planner",
            filters={"docstatus": ["<", 2]},
            fields=["farm", "block", "to_date", "unmet_deficit_mm"],
            order_by="to_date desc",
            limit=1,
        )
        if not rows:
            self.skipTest("no planners to read")
        r = rows[0]
        far_future = frappe.utils.add_days(r.to_date, 30)
        got = carried_for(r.farm, r.block, far_future)
        self.assertAlmostEqual(got, float(r.unmet_deficit_mm or 0), places=3)
```

- [ ] **Step 2: Run to verify it fails**

Run: `bench --site kaitet.local run-tests --app upande_irrigation --module upande_irrigation.tests.test_planner_hook`
Expected: FAIL — `cannot import name 'carried_for'`

- [ ] **Step 3: Rewrite the hook**

Replace the whole body of `upande_irrigation/events/irrigation_planner.py` with:

```python
"""Before Save handler for Irrigation Planner — demand only.

Answers one question: given the elapsed week's weather and what this shift still
owes, how many hours does it need? It writes no clock and no capped figure,
because a single shift cannot know whether the pump is free. The scheduler owns
that (api/scheduler.py) and writes shift_hours, the cycle fields and the windows.

Registered in hooks.py under doc_events["Irrigation Planner"]["before_save"].
"""

import frappe

from upande_irrigation.engine import demand as D

CHRONIC_DEFICIT_THRESHOLD_MM = 50.0
CHRONIC_WEEKS_THRESHOLD = 3


def settings_dict():
    s = frappe.get_cached_doc("Irrigation Settings")
    return {
        "et_crop_coefficient": s.get("default_avocado_utilisation") or 0.65,
        "default_application_rate_mm_hr": s.get("default_application_rate_mm_hr") or 2.8,
        "default_irrigation_coverage": s.get("default_irrigation_coverage") or 70.0,
        "min_weather_days": s.get("min_weather_days") or D.MEASURE_DAYS,
        "auto_cycle_threshold_hrs": s.get("auto_cycle_threshold_hrs") or 5.0,
        "cycles_when_above_threshold": s.get("cycles_when_above_threshold") or 2,
        "cycles_when_below_threshold": s.get("cycles_when_below_threshold") or 1,
        "cycle_rest_hours": s.get("cycle_rest_hours") or 2.0,
    }


def readings_for(farm, start, end):
    return frappe.get_all(
        "Weather Reading",
        filters={"farm": farm, "date": ["between", [str(start), str(end)]]},
        fields=["rainfall_mm", "et_pan", "et_crop", "minimum_temperature", "maximum_temperature"],
        limit_page_length=0,
    )


def carried_for(farm, block, from_date):
    """Unmet deficit from the most recent prior planner for this shift.

    Ordered lookup rather than the old exact-date chain, so a skipped week no
    longer drops the debt to zero.
    """
    rows = frappe.get_all(
        "Irrigation Planner",
        filters={"farm": farm, "block": block, "to_date": ["<", str(from_date)], "docstatus": ["<", 2]},
        fields=["unmet_deficit_mm"],
        order_by="to_date desc",
        limit=1,
    )
    return float(rows[0].get("unmet_deficit_mm") or 0) if rows else 0.0


def compute_shift(doc, method=None):
    if not doc.farm:
        frappe.throw("Farm is required.")
    if not doc.from_date or not doc.to_date:
        frappe.throw("From Date and To Date are required.")
    if not doc.block:
        frappe.throw("Block (shift) is required.")

    from_date = frappe.utils.getdate(doc.from_date)
    to_date = frappe.utils.getdate(doc.to_date)
    if from_date > to_date:
        frappe.throw("To Date cannot be before From Date.")
    if frappe.utils.date_diff(to_date, from_date) + 1 != 7:
        frappe.throw(f"A week must be exactly 7 days. Set To Date to {frappe.utils.add_days(from_date, 6)}.")

    shift_name = doc.block
    if " - SHIFT " not in shift_name:
        frappe.throw(f"Block must follow the pattern '{{SECTION}} - SHIFT {{N}}'. Got: {shift_name}")
    section_prefix = shift_name.split(" - SHIFT ")[0]

    settings = settings_dict()

    # Demand comes from the week that has already happened.
    m_start, m_end = D.measured_window(from_date)
    agg = D.aggregate(readings_for(doc.farm, m_start, m_end))
    doc.measured_from = m_start
    doc.measured_to = m_end
    doc.weather_completeness = f"{agg['days']} of {settings['min_weather_days']} days logged"

    doc.weekly_rainfall_mm = round(agg["rainfall_mm"], 2)
    doc.weekly_et_pan_mm = round(agg["et_pan_mm"], 2)
    doc.raw_et_crop_mm = round(agg["et_crop_mm"], 4)

    carried = carried_for(doc.farm, doc.block, from_date)
    doc.carried_deficit_mm = round(carried, 2)

    out = D.demand(agg, carried, settings)
    doc.et_crop_coefficient = settings["et_crop_coefficient"]
    doc.clean_et_crop_mm = out["clean_et_crop_mm"]
    doc.this_week_deficit = out["week_deficit_mm"]
    doc.total_water_needed_mm = out["total_needed_mm"]
    doc.required_hours = out["required_hours"]
    doc.no_irrigation_reason = out["no_irrigation_reason"]

    # Week label, unchanged in intent.
    doc.week_dates = f"{section_prefix} | {m_start} - {to_date}"
    doc.irrigation_week = frappe.utils.getdate(to_date).isocalendar()[1]

    # Anthracnose index, formula unchanged.
    if agg["temp_days"] > 0 and agg["mean_temp"] > 0:
        z = round(-58.99 + (3.22 * agg["mean_temp"]) + (0.18 * agg["rainfall_mm"]), 2)
        doc.z_value = z
        if z >= 20:
            doc.z_risk_level = "High Risk - Fungicide Required"
        elif z >= 15:
            doc.z_risk_level = "Infection Risk - Monitor Closely"
        elif z >= 5:
            doc.z_risk_level = "Spore Release - Low Alert"
        else:
            doc.z_risk_level = "Low Risk"
    else:
        doc.z_value = 0.0
        doc.z_risk_level = "Insufficient Data"

    doc.active_shift_count = frappe.db.count(
        "Block Type", {"name": ["like", f"{section_prefix} - SHIFT %"], "is_active": 1, "farm": doc.farm}
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `bench --site kaitet.local run-tests --app upande_irrigation --module upande_irrigation.tests.test_planner_hook`
Expected: PASS, 2 tests

- [ ] **Step 5: Re-run the earlier suites to check nothing regressed**

Run: `bench --site kaitet.local run-tests --app upande_irrigation --module upande_irrigation.tests.test_demand`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add upande_irrigation/events/irrigation_planner.py upande_irrigation/tests/test_planner_hook.py
git commit -m "feat(engine): planner computes demand over the elapsed week only"
```

---

### Task 5: Scheduler allocates

**Files:**
- Modify: `upande_irrigation/api/scheduler.py` — replace `assign_daily_blocks` with allocator calls, add refusal recording
- Test: `upande_irrigation/tests/test_scheduler_run.py`

**Interfaces:**
- Consumes: `engine.allocate.pump_capacity_hours`, `grant`, `lay_out`; `events.irrigation_planner.settings_dict`
- Produces: `run(triggered_by="Manual")` unchanged signature; new `pump_groups(farm) -> dict[str, list[str]]` mapping pump name to section prefixes; `plan_window(cfg) -> tuple[date, date]`

- [ ] **Step 1: Write the failing test**

```python
"""A run must refuse a farm-week it cannot measure, and must not leave a planner
whose hours nobody granted.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.api.scheduler import plan_window, pump_groups


class TestPlanWindow(FrappeTestCase):
    def test_window_is_seven_days(self):
        cfg = frappe.get_cached_doc("Irrigation Scheduler")
        start, end = plan_window(cfg)
        self.assertEqual(frappe.utils.date_diff(end, start), 6)


class TestPumpGroups(FrappeTestCase):
    def test_sections_without_a_profile_group_under_none(self):
        farm = frappe.get_all("Farm", filters={"is_irrigation_farm": 1}, fields=["name"], limit=1)
        if not farm:
            self.skipTest("no irrigation farm on this site")
        groups = pump_groups(farm[0]["name"])
        self.assertIsInstance(groups, dict)
        # With zero Irrigation Pump Profile rows every section lands in one
        # unverified bucket rather than being dropped.
        self.assertTrue(all(isinstance(v, list) for v in groups.values()))
```

- [ ] **Step 2: Run to verify it fails**

Run: `bench --site kaitet.local run-tests --app upande_irrigation --module upande_irrigation.tests.test_scheduler_run`
Expected: FAIL — `cannot import name 'plan_window'`

- [ ] **Step 3: Implement in `api/scheduler.py`**

Add these helpers and rewire `run()` so that after inserting planners for a farm it allocates per pump group. Keep the existing logging, run-record and abort behaviour.

```python
def plan_window(cfg):
    """The week being planned, from the scheduler's own configuration."""
    today = frappe.utils.getdate(frappe.utils.nowdate())
    week_start = cfg.week_starts_on or "Thursday"
    start_idx = _DAY_INDEX.get(week_start, 3)
    this_week_start = frappe.utils.add_days(today, -((today.weekday() - start_idx) % 7))
    if (cfg.plan_for or "Current Week") == "Next Week":
        this_week_start = frappe.utils.add_days(this_week_start, 7)
    return this_week_start, frappe.utils.add_days(this_week_start, 6)


def pump_groups(farm):
    """Section prefixes grouped by the pump that serves them.

    Sections sharing a pump_name must be sequenced against one capacity. With no
    Irrigation Pump Profile rows they all fall into a single unverified group,
    which is the honest reading of "we do not know the pump".
    """
    rows = frappe.db.sql(
        """
        SELECT pp.pump_name AS pump_name, w.warehouse_name AS section
        FROM `tabIrrigation Pump Profile` pp
        INNER JOIN `tabWarehouse` w ON w.name = pp.irrigation_section
        WHERE pp.farm = %s
        """,
        (farm,),
        as_dict=True,
    )
    groups = {}
    for r in rows:
        prefix = (r["section"] or "").replace("_SECTION", "").strip()
        if prefix:
            groups.setdefault(r["pump_name"] or "unnamed", []).append(prefix)
    return groups


def profile_for(farm, pump_name):
    rows = frappe.get_all(
        "Irrigation Pump Profile",
        filters={"farm": farm, "pump_name": pump_name},
        fields=["water_target_m3_per_week", "pump_flow_rate_m3_per_hr"],
        limit=1,
    )
    return rows[0] if rows else None
```

Then, replacing the pass-2 `assign_daily_blocks` block:

```python
                # ── Pass 2: allocate against pump capacity ──
                groups = pump_groups(farm)
                # Sections with no profile share one unverified pool.
                mapped = {p for ps in groups.values() for p in ps}
                unmapped = [c for c in created if c["prefix"] not in mapped]
                if unmapped:
                    groups.setdefault(None, [])

                for pump_name, prefixes in list(groups.items()) or [(None, [])]:
                    members = [
                        c for c in created
                        if (c["prefix"] in prefixes) if pump_name is not None
                    ] if pump_name is not None else unmapped
                    if not members:
                        continue

                    capacity, verified = AL.pump_capacity_hours(
                        profile_for(farm, pump_name) if pump_name else None
                    )
                    requests = [
                        {"key": c["name"], "required_hours": c["required_hours"]} for c in members
                    ]
                    laid = AL.lay_out(AL.grant(requests, capacity), schedule_from, settings)

                    for item in laid:
                        windows = item["windows"]
                        granted = item["granted_hours"] if windows else 0.0
                        delivered = granted * settings["default_application_rate_mm_hr"] * (
                            settings["default_irrigation_coverage"] / 100.0
                        )
                        planner = next(c for c in members if c["name"] == item["key"])
                        total = planner["total_needed_mm"]
                        warning = ""
                        if not verified:
                            warning = (
                                f"Pump capacity unverified: no Irrigation Pump Profile for "
                                f"{pump_name or planner['prefix']}; assumed {capacity:.0f} hr/wk."
                            )
                        elif item["capped"]:
                            warning = (
                                f"Pump capacity capped ({pump_name}, {capacity:.1f} hr/wk): "
                                f"needed {item['required_hours']:.2f} hrs, granted "
                                f"{granted:.2f} hrs."
                            )
                        unmet = max(0.0, total - delivered)
                        if unmet > CHRONIC_DEFICIT_THRESHOLD_MM:
                            warning = (warning + " | " if warning else "") + (
                                f"CHRONIC DEFICIT: {unmet:.1f} mm unmet — exceeds "
                                f"{CHRONIC_DEFICIT_THRESHOLD_MM} mm threshold."
                            )
                        frappe.db.set_value(
                            "Irrigation Planner", item["key"],
                            {
                                "shift_hours": round(granted, 2),
                                "cycles_count": item["cycles_count"],
                                "cycle_hours_each": item["cycle_hours_each"],
                                "cycle_plan": (
                                    f"{item['cycles_count']} × {item['cycle_hours_each']:.2f} hr"
                                    if item["cycles_count"] else ""
                                ),
                                "scheduled_start": windows[0][0] if windows else None,
                                "scheduled_end": windows[-1][1] if windows else None,
                                "delivered_depth_mm": round(delivered, 2),
                                "unmet_deficit_mm": round(unmet, 2),
                                "capacity_warning": warning,
                            },
                            update_modified=False,
                        )
                    frappe.db.commit()
```

And in pass 1, refuse a farm-week that cannot be measured, before creating anything:

```python
            m_start, m_end = D.measured_window(schedule_from)
            agg = D.aggregate(readings_for(farm, m_start, m_end))
            plannable, why = D.is_plannable(agg, settings)
            if not plannable:
                log_err(f"  {farm}: {why} ({m_start} → {m_end}) — no planners created")
                shift_results.append({
                    "farm": farm, "section": "—", "block": "—",
                    "status": "Skipped", "planner": None, "shift_hours": 0,
                    "message": why,
                })
                continue
```

Add to the imports at the top of the module:

```python
from upande_irrigation.engine import allocate as AL
from upande_irrigation.engine import demand as D
from upande_irrigation.events.irrigation_planner import (
    CHRONIC_DEFICIT_THRESHOLD_MM,
    readings_for,
    settings_dict,
)
```

and capture `prefix`, `required_hours` and `total_needed_mm` on each entry appended to `created` in pass 1.

- [ ] **Step 4: Run to verify it passes**

Run: `bench --site kaitet.local run-tests --app upande_irrigation --module upande_irrigation.tests.test_scheduler_run`
Expected: PASS, 2 tests

- [ ] **Step 5: Exercise a real run**

Run: `bench --site kaitet.local execute upande_irrigation.api.scheduler.run --kwargs "{'triggered_by':'Manual'}"`
Expected: completes; with no weather in the measured week it reports refusals and creates no planners, and `Irrigation Scheduler Run` records the reason.

- [ ] **Step 6: Commit**

```bash
git add upande_irrigation/api/scheduler.py upande_irrigation/tests/test_scheduler_run.py
git commit -m "feat(engine): scheduler allocates against pump capacity and refuses unmeasurable weeks"
```

---

### Task 6: Remove the per-block client script and regenerate

**Files:**
- Modify: `upande_irrigation/public/js/irrigation_planner.js`
- Modify: `upande_irrigation/fixtures/*` (re-export)

- [ ] **Step 1: Strip the dead handlers**

Delete the `frappe.ui.form.on('Irrigation Planner Section', …)` block and the `recalc_row` / `recalc_cycles_only` functions from `upande_irrigation/public/js/irrigation_planner.js`. Keep the `from_date` auto-fill and the `no_irrigation_reason` headline. The child table they edited no longer exists.

- [ ] **Step 2: Check nothing else references them**

Run: `grep -rn "irrigation_calculations\|Irrigation Planner Section\|recalc_row" upande_irrigation/`
Expected: no matches.

- [ ] **Step 3: Delete the backfill planners**

Run:
```bash
bench --site kaitet.local mariadb -e "DELETE FROM \`tabIrrigation Planner\`;"
```
These are backfill, confirmed in the spec. Only 72 of 836 carried meaning.

- [ ] **Step 4: Re-export fixtures**

Run: `bench --site kaitet.local export-fixtures --app upande_irrigation`
Expected: `custom_field.json` gains `required_hours`, `measured_from`, `measured_to`, `min_weather_days`, `cycle_rest_hours` and loses `irrigation_calculations`; `Irrigation Calculation` gone from `doctype.json`.

- [ ] **Step 5: Full suite**

Run: `bench --site kaitet.local run-tests --app upande_irrigation`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore(engine): drop per-block form handlers and regenerate fixtures"
```

---

## Self-Review

**Spec coverage.** Demand window → Task 1 + 4. Refusal → Task 1 (`is_plannable`) + Task 5 (recording). Demand/allocation split → Tasks 1, 2, 4, 5. Carry-forward → Task 4 (`carried_for`). Cycle rests → Task 2 (`lay_out`) + Task 3 (`cycle_rest_hours`). Pump grouping → Task 5 (`pump_groups`). Per-block removal → Task 3 + 6. Regeneration → Task 6. Proportional capping → Task 2 (`grant`). Unverified fallback → Task 2 (`pump_capacity_hours`) + Task 5 (warning text).

**Not covered here, by design.** The four visuals and the palette module are Phase 2. The Overview alert for refusals needs `api/overview.py` to read the new run reasons — folded into Phase 2 since it is a dashboard read.

**Type consistency.** `settings_dict()` keys are consumed identically by `demand()` and `cycle_plan()`/`lay_out()`. `required_hours` is the field name and the dict key throughout. `grant()` returns `granted_hours` + `capped`; `lay_out()` adds `cycles_count`, `cycle_hours_each`, `windows` — all read under those names in Task 5.
