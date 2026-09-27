"""Overview API — the dashboard's landing tiles and alert list.

One call returns everything /upande-irrigation#overview paints. Since 2026-09-28
it reads the daily water balance (api/balance.py) and the run sheet
(api/runsheet.py); the weekly Irrigation Planner it used to read is retired.

Callable as: frappe.call({method: 'upande_irrigation.api.overview.fetch'})
"""

import frappe

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# A sensor silent for longer than this is treated as stale — same 6 h the IoT
# view uses for its Stale badge.
_STALE_SENSOR_HOURS = 6

# Operators log one Weather Reading a day; two missed days is worth surfacing
# because every downstream number derives from it.
_MISSING_READING_DAYS = 2

# A block this far past its irrigation point (depletion ÷ RAW) is under stress.
_STRESS_RATIO = 1.5


def _guard(label, fn, default=None):
    """Run one tile/alert query, logging and skipping it if it fails.

    Several doctypes this reads (Sensor Readings, the meter doctypes) are owned
    by other apps and may not exist on a given site. A missing one should cost
    its own tile, not the whole dashboard.
    """
    try:
        return fn()
    except Exception:
        frappe.log_error(title=f"Upande Irrigation overview — {label} failed")
        return default


def _plural(n, one, many):
    """"1 shift" / "3 shifts" — never "1 shift(s)"."""
    return f"{n} {one if n == 1 else many}"


def _alert(severity, title, detail, route=None, count=None):
    return {
        "severity": severity,
        "title": title,
        "detail": detail,
        "route": route,
        "count": count,
    }


@frappe.whitelist()
def fetch(farm=None):
    from upande_irrigation.api import balance as BA
    from upande_irrigation.api import runsheet as RS

    today = frappe.utils.getdate()
    from_date, to_date = str(today), str(frappe.utils.add_days(today, 6))

    tiles = []
    alerts = []

    state = _guard("block states", lambda: BA.state(farm), default={}) or {}
    blocks = list(state.values())
    sheets = (_guard("run sheet", lambda: RS.today(farm), default={"sheets": []}) or {}).get("sheets") or []
    runs = [r for sh in sheets for r in sh["runs"]]

    now = frappe.utils.now_datetime()

    def running_now():
        live = [r for r in runs if r.get("status") in ("Planned", "Running") and r.get("planned_start")
                and frappe.utils.get_datetime(r["planned_start"]) <= now <= frappe.utils.get_datetime(r["planned_end"])]
        live += [r for r in runs if r.get("status") == "Running" and r not in live]
        return {"shifts": len({r["shift"] for r in live}), "sections": len({r.get("section") for r in live})}

    live = running_now()

    def valves_on():
        from upande_irrigation.api.valves import list_states

        data = list_states(farm=farm)
        valves = data.get("valves") or []
        return {
            "total": len(valves),
            "on": sum(1 for v in valves if v.get("effective_state") == "ON"),
            "overrides": sum(1 for v in valves if v.get("override_active")),
        }

    valves = _guard("valve states", valves_on) or {"total": 0, "on": 0, "overrides": 0}

    due = [b for b in blocks if b["raw_mm"] and b["depletion_mm"] >= b["raw_mm"]]
    stressed = [b for b in blocks if b["raw_mm"] and b["depletion_mm"] >= _STRESS_RATIO * b["raw_mm"]]
    mean_pct = round(sum(b["depletion_pct"] for b in blocks) / len(blocks), 0) if blocks else None
    planned = [r for r in runs if r.get("status") in ("Planned", "Running", "Done", "Partial")]
    unplaced = [r for r in runs if r.get("status") == "Not placed"]
    planned_h = round(sum(float(r.get("planned_hours") or 0) for r in planned), 1)

    tiles = [
        {"key": "irrigating", "label": "Irrigating now", "value": live["sections"], "unit": "sections",
         "note": _plural(live["shifts"], "shift", "shifts") + " running" if live["shifts"] else "nothing running right now",
         "tone": "ok" if live["sections"] else "ink"},
        {"key": "due", "label": "Blocks due", "value": len(due), "unit": f"of {len(blocks)}",
         "note": "at or past their irrigation point" if blocks else "no block profiles yet",
         "tone": "hot" if stressed else ("warn" if due else "ok")},
        {"key": "depletion", "label": "Root-zone depletion", "value": mean_pct, "unit": "%",
         "note": "mean across blocks, of available water", "tone": "clay"},
        {"key": "today", "label": "Planned today", "value": planned_h, "unit": "h",
         "note": (f"{len({r['shift'] for r in planned})} shifts" + (f" · {len(unplaced)} not placed" if unplaced else ""))
         if runs else "no run sheet yet today", "tone": "warn" if unplaced else "ok"},
        {"key": "valves", "label": "Valves open", "value": valves["on"], "unit": f"of {valves['total']}",
         "note": _plural(valves["overrides"], "operator override", "operator overrides") if valves["overrides"] else "following the run sheet",
         "tone": "ok" if valves["on"] else "ink"},
    ]

    def stress():
        if not stressed:
            return None
        worst = max(stressed, key=lambda b: b["depletion_mm"] / b["raw_mm"])
        return _alert("hot", _plural(len(stressed), "block", "blocks") + " well past their irrigation point",
                      f"Worst: {worst['label']} at {worst['depletion_pct']:.0f}% depletion (irrigate at "
                      f"{100 * worst['raw_mm'] / worst['taw_mm']:.0f}%). If these were irrigated, record it on the run sheet.",
                      route="/upande-irrigation#planner?tab=blocks", count=len(stressed))

    def not_placed():
        if not unplaced:
            return None
        hours = sum(float(r.get("planned_hours") or 0) for r in unplaced)
        return _alert("warn", _plural(len({r['shift'] for r in unplaced}), "shift", "shifts") + " could not be placed today",
                      f"{hours:.1f} h did not fit the pumps' run windows. Widen a window in Irrigation Pump Profile, or they carry to tomorrow.",
                      route="/upande-irrigation#planner", count=len(unplaced))

    def skipped():
        rows = [r for r in runs if r.get("status") == "Skipped"]
        if not rows:
            return None
        return _alert("clay", _plural(len(rows), "cycle", "cycles") + " skipped today",
                      "; ".join(f"{r['shift']}: {r.get('skip_reason') or 'no reason'}" for r in rows[:3]),
                      route="/upande-irrigation#planner", count=len(rows))

    def no_sheet():
        if blocks and not any(sh.get("run_sheet") for sh in sheets):
            return _alert("clay", "No run sheet for today yet",
                          "It is generated each morning at the Irrigation Scheduler's run hour — or generate it now from the Irrigation plan.",
                          route="/upande-irrigation#planner")
        return None

    def on_defaults():
        rows = [b for b in blocks if {"soil_texture", "planting_year", "emitter_flow_lph"} & set(b["using_defaults"])]
        if not rows:
            return None
        return _alert("clay", _plural(len(rows), "block", "blocks") + " using farm defaults",
                      "Soil, planting year or emitters are blank on their Irrigation Block Profile, so their "
                      "water holding and application rate are assumptions.",
                      route="/app/irrigation-block-profile?using_defaults=%5B%22is%22%2C%22set%22%5D", count=len(rows))

    # ── Alert: disease risk from the latest reading per farm ────
    def disease_risk():
        rows = frappe.db.sql(
            """
            SELECT w.farm, w.z_value, w.z_risk_level, w.date
            FROM `tabWeather Reading` w
            INNER JOIN (
                SELECT farm, MAX(date) AS latest
                FROM `tabWeather Reading`
                GROUP BY farm
            ) m ON m.farm = w.farm AND m.latest = w.date
            WHERE w.z_value >= 15
              AND (%(farm)s IS NULL OR w.farm = %(farm)s)
            ORDER BY w.z_value DESC
            """,
            {"farm": farm},
            as_dict=True,
        )
        if not rows:
            return None
        worst = rows[0]
        severity = "hot" if float(worst["z_value"] or 0) >= 20 else "warn"
        return _alert(
            severity,
            f"Anthracnose risk: {worst['z_risk_level']}",
            f"{worst['farm']} at z = {round(float(worst['z_value'] or 0), 1)} "
            f"on {worst['date']}.",
            route="/app/weather-reading",
            count=len(rows),
        )

    # ── Alert: missing weather readings ─────────────────────────
    def missing_readings():
        rows = frappe.db.sql(
            """
            SELECT farm, MAX(date) AS latest, DATEDIFF(CURDATE(), MAX(date)) AS gap
            FROM `tabWeather Reading`
            WHERE (%(farm)s IS NULL OR farm = %(farm)s)
            GROUP BY farm
            HAVING gap >= %(gap)s
            ORDER BY gap DESC
            """,
            {"farm": farm, "gap": _MISSING_READING_DAYS},
            as_dict=True,
        )
        if not rows:
            return None
        worst = rows[0]
        return _alert(
            "clay",
            f"No weather reading for {int(worst['gap'])} days",
            f"{worst['farm']} last logged {worst['latest']}. ET, SWD and next week's "
            "planners all depend on the daily reading — log it from the Weather view.",
            route="/upande-irrigation#weather",
            count=len(rows),
        )

    # ── Alert: stale sensors ────────────────────────────────────
    def stale_sensors():
        rows = frappe.db.sql(
            """
            SELECT deveui, sensor_name, MAX(timestamp) AS last_seen,
                   TIMESTAMPDIFF(HOUR, MAX(timestamp), NOW()) AS hours_silent
            FROM `tabSensor Readings`
            GROUP BY deveui, sensor_name
            HAVING hours_silent > %(hours)s
            ORDER BY hours_silent DESC
            """,
            {"hours": _STALE_SENSOR_HOURS},
            as_dict=True,
        )
        if not rows:
            return None
        worst = rows[0]
        return _alert(
            "warn",
            _plural(len(rows), "sensor", "sensors") + f" silent over {_STALE_SENSOR_HOURS} h",
            f"Longest: {worst['sensor_name'] or worst['deveui']} at "
            f"{int(worst['hours_silent'] or 0)} h.",
            route="/upande-irrigation#iot",
            count=len(rows),
        )

    def scheduler_health():
        cfg = frappe.db.get_value("Irrigation Scheduler", "Irrigation Scheduler", ["enabled", "auto_run_enabled"], as_dict=True)
        if not cfg:
            return None
        if not cfg.get("enabled"):
            return _alert("clay", "Scheduler is disabled", "No run sheets will be generated until it is enabled.",
                          route="/app/irrigation-scheduler")
        if not cfg.get("auto_run_enabled"):
            return _alert("warn", "Auto-run is off", "The morning run sheet will not be generated automatically.",
                          route="/app/irrigation-scheduler")
        return None

    # ── The next 7 days, as a section × day grid, from the projection ─
    def schedule_grid():
        wk = RS.week(farm)
        start = today
        sections = {}
        for f in wk["farms"]:
            for sh in f["shifts"]:
                sec = sections.setdefault(sh["section"] or "?", {
                    "section": sh["section"] or "?", "farm": f["farm"], "days": [[] for _ in range(7)],
                    "coverage_pct": None, "needed_mm_per_shift": None, "delivered_mm_per_shift": None})
                for i, c in enumerate(sh["cells"]):
                    if c["due"]:
                        sec["days"][i].append({"planner": None, "shift": sh["shift"], "hours": c["hours"],
                                               "cycles": 0, "state": "scheduled" if i else "running" if live["shifts"] else "scheduled",
                                               "starts_at": None})
        if not sections:
            return None
        # Water replaced over the last 7 days: recorded net irrigation ÷ crop use.
        rows = frappe.db.sql(
            """SELECT w.parent_warehouse AS section, SUM(b.irrigation_mm) AS irr, SUM(b.etc_mm) AS etc
               FROM `tabIrrigation Block Balance` b JOIN `tabWarehouse` w ON w.name = b.block
               WHERE b.date >= %(d)s GROUP BY w.parent_warehouse""",
            {"d": frappe.utils.add_days(today, -7)}, as_dict=True)
        for r in rows:
            if r.section in sections and r.etc:
                sections[r.section]["coverage_pct"] = round(100.0 * float(r.irr or 0) / float(r.etc), 1)
        days = []
        for i in range(7):
            d = frappe.utils.getdate(frappe.utils.add_days(start, i))
            days.append({"date": str(d), "label": f"{_DAY_NAMES[d.weekday()][:3]} {d.day}", "today": i == 0})
        return {"days": days, "sections": sorted(sections.values(), key=lambda s: s["section"])}

    grid = _guard("schedule grid", schedule_grid)

    # ── Mean root-zone depletion, last 30 days ──────────────────
    def deficit_trend(days=30):
        rows = frappe.db.sql(
            """SELECT date, AVG(depletion_pct) AS pct FROM `tabIrrigation Block Balance`
               WHERE date >= %(d)s AND (%(farm)s IS NULL OR farm = %(farm)s)
               GROUP BY date ORDER BY date""",
            {"d": frappe.utils.add_days(today, -days), "farm": farm}, as_dict=True)
        if not rows:
            return None
        return {"labels": [str(r.date) for r in rows], "values": [round(float(r.pct or 0), 1) for r in rows],
                "unit": "% of available water"}

    trend = _guard("depletion trend", deficit_trend)

    for label, fn in (
        ("stress", stress),
        ("not placed", not_placed),
        ("skipped", skipped),
        ("no run sheet", no_sheet),
        ("disease risk", disease_risk),
        ("missing readings", missing_readings),
        ("stale sensors", stale_sensors),
        ("on defaults", on_defaults),
        ("scheduler health", scheduler_health),
    ):
        found = _guard(label, fn)
        if found:
            alerts.append(found)

    severity_rank = {"hot": 0, "warn": 1, "clay": 2, "ok": 3}
    alerts.sort(key=lambda a: severity_rank.get(a["severity"], 9))

    etc7 = [b["mean_etc_mm"] for b in blocks]
    return {
        "week": {
            "from_date": from_date,
            "to_date": to_date,
            "planners": len(planned),
            "farms": len({b["farm"] for b in blocks}),
            "shift_hours": planned_h,
            "rainfall_mm": None,
            "et_crop_mm": round(7 * sum(etc7) / len(etc7), 1) if etc7 else None,
        },
        "tiles": tiles,
        "alerts": alerts,
        "schedule": grid,
        "deficit_trend": trend,
        "generated_at": frappe.utils.get_datetime_str(frappe.utils.now_datetime()),
        "farm_filter": farm or "all",
    }
