"""Overview API — the dashboard's landing tiles and alert list.

One call returns everything /upande-irrigation#overview paints, so the landing
view is a single round trip instead of four full data pulls aggregated in the
browser.

The alert rules live here rather than in JavaScript because they read the same
thresholds the planning engine writes (events/irrigation_planner.py) — chronic
deficit at 50 mm, the pump-capping and persistent-deficit warning strings, the
Z-value risk bands. Keeping them next to the engine means one place to change
when the agronomy changes.

Callable as: frappe.call({method: 'upande_irrigation.api.overview.fetch'})
"""

import frappe

from upande_irrigation.events.irrigation_planner import CHRONIC_DEFICIT_THRESHOLD_MM

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_DAY_INDEX = {n: i for i, n in enumerate(_DAY_NAMES)}

# A sensor silent for longer than this is treated as stale — same 6 h the IoT
# view uses for its Stale badge.
_STALE_SENSOR_HOURS = 6

# Operators log one Weather Reading a day; two missed days is worth surfacing
# because every downstream number derives from it.
_MISSING_READING_DAYS = 2


def _week_window():
    """The planning week, resolved the same way api.scheduler.run does."""
    today = frappe.utils.getdate(frappe.utils.nowdate())
    week_start = "Thursday"
    try:
        week_start = frappe.db.get_single_value("Irrigation Scheduler", "week_starts_on") or week_start
    except Exception:
        pass
    start_idx = _DAY_INDEX.get(week_start, 3)
    days_since_start = (today.weekday() - start_idx) % 7
    from_date = frappe.utils.add_days(today, -days_since_start)
    return str(from_date), str(frappe.utils.add_days(from_date, 6)), week_start


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
    from_date, to_date, week_start = _week_window()

    farm_clause = " AND p.farm = %(farm)s" if farm else ""
    args = {"from_date": from_date, "to_date": to_date, "farm": farm}

    tiles = []
    alerts = []

    # ── Week totals across this week's planners ─────────────────
    def week_totals():
        return frappe.db.sql(
            f"""
            SELECT
                COUNT(*)                                  AS planners,
                COALESCE(SUM(p.total_water_needed_mm), 0) AS needed,
                COALESCE(SUM(p.delivered_depth_mm), 0)    AS delivered,
                COALESCE(SUM(p.unmet_deficit_mm), 0)      AS unmet,
                COALESCE(SUM(p.shift_hours), 0)           AS hours,
                COALESCE(AVG(p.weekly_rainfall_mm), 0)    AS rainfall,
                COALESCE(AVG(p.clean_et_crop_mm), 0)      AS et_crop,
                COUNT(DISTINCT p.farm)                    AS farms
            FROM `tabIrrigation Planner` p
            WHERE p.docstatus < 2
              AND p.from_date = %(from_date)s
              AND p.to_date = %(to_date)s
              {farm_clause}
            """,
            args,
            as_dict=True,
        )[0]

    totals = _guard("week totals", week_totals) or {}

    # ── Sections irrigating right now ───────────────────────────
    def irrigating_now():
        now_str = frappe.utils.get_datetime_str(frappe.utils.now_datetime())
        rows = frappe.db.sql(
            f"""
            SELECT p.block AS shift, p.farm AS farm
            FROM `tabIrrigation Planner` p
            WHERE p.docstatus < 2
              AND p.scheduled_start IS NOT NULL
              AND p.scheduled_end IS NOT NULL
              AND %(now)s BETWEEN p.scheduled_start AND p.scheduled_end
              {farm_clause}
            """,
            {"now": now_str, "farm": farm},
            as_dict=True,
        )
        sections = {
            (r["shift"] or "").split(" - SHIFT ")[0] for r in rows if r.get("shift")
        }
        return {"shifts": len(rows), "sections": len(sections)}

    live = _guard("irrigating now", irrigating_now) or {"shifts": 0, "sections": 0}

    # ── Valves on now ───────────────────────────────────────────
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

    # ── Tiles ───────────────────────────────────────────────────
    needed = float(totals.get("needed") or 0)
    delivered = float(totals.get("delivered") or 0)
    coverage = round(100.0 * delivered / needed, 1) if needed > 0 else None

    tiles = [
        {
            "key": "irrigating",
            "label": "Irrigating now",
            "value": live["sections"],
            "unit": "sections",
            "note": _plural(live["shifts"], "shift", "shifts") + " running"
            if live["shifts"]
            else "nothing running right now",
            "tone": "ok" if live["sections"] else "ink",
        },
        {
            "key": "demand",
            "label": "Week demand",
            "value": round(needed, 1),
            "unit": "mm",
            "note": _plural(int(totals.get("planners") or 0), "planner", "planners") + " this week"
            if totals.get("planners")
            else "no planners generated yet",
            "tone": "clay",
        },
        {
            "key": "delivered",
            "label": "Delivered",
            "value": round(delivered, 1),
            "unit": "mm",
            "note": f"{coverage:g}% of demand" if coverage is not None else "no demand yet",
            "tone": "ok" if coverage is not None and coverage >= 95 else "warn",
        },
        {
            "key": "unmet",
            "label": "Carrying over",
            "value": round(float(totals.get("unmet") or 0), 1),
            "unit": "mm",
            "note": "unmet, rolls to next week",
            "tone": "hot" if float(totals.get("unmet") or 0) > 0 else "ok",
        },
        {
            "key": "valves",
            "label": "Valves open",
            "value": valves["on"],
            "unit": f"of {valves['total']}",
            "note": _plural(valves["overrides"], "operator override", "operator overrides")
            if valves["overrides"]
            else "all on schedule",
            "tone": "ok" if valves["on"] else "ink",
        },
    ]

    # ── Alert: pump-capped shifts ───────────────────────────────
    def pump_capped():
        rows = frappe.db.sql(
            f"""
            SELECT p.name, p.block, p.farm, p.unmet_deficit_mm
            FROM `tabIrrigation Planner` p
            WHERE p.docstatus < 2
              AND p.from_date = %(from_date)s
              AND p.to_date = %(to_date)s
              AND p.capacity_warning LIKE '%%Pump capacity capped%%'
              {farm_clause}
            """,
            args,
            as_dict=True,
        )
        if not rows:
            return None
        carry = sum(float(r["unmet_deficit_mm"] or 0) for r in rows)
        return _alert(
            "warn",
            _plural(len(rows), "shift", "shifts") + " capped by pump capacity",
            f"{round(carry, 1)} mm carries to next week. Review Irrigation Pump Profile "
            f"targets or the shift split for these sections.",
            route="/app/irrigation-planner?capacity_warning=%25Pump%20capacity%20capped%25",
            count=len(rows),
        )

    # ── Alert: chronic deficit ──────────────────────────────────
    def chronic():
        rows = frappe.db.sql(
            f"""
            SELECT p.name, p.block, p.farm, p.unmet_deficit_mm
            FROM `tabIrrigation Planner` p
            WHERE p.docstatus < 2
              AND p.from_date = %(from_date)s
              AND p.to_date = %(to_date)s
              AND p.unmet_deficit_mm > %(threshold)s
              {farm_clause}
            ORDER BY p.unmet_deficit_mm DESC
            """,
            dict(args, threshold=CHRONIC_DEFICIT_THRESHOLD_MM),
            as_dict=True,
        )
        if not rows:
            return None
        worst = rows[0]
        return _alert(
            "hot",
            _plural(len(rows), "shift", "shifts")
            + f" over the {CHRONIC_DEFICIT_THRESHOLD_MM:.0f} mm deficit threshold",
            f"Worst: {worst['block']} at {round(float(worst['unmet_deficit_mm'] or 0), 1)} mm unmet. "
            "Trees may be stressed.",
            route=f"/app/irrigation-planner/{worst['name']}",
            count=len(rows),
        )

    # ── Alert: persistent deficit ───────────────────────────────
    def persistent():
        rows = frappe.db.sql(
            f"""
            SELECT p.name, p.block, p.farm
            FROM `tabIrrigation Planner` p
            WHERE p.docstatus < 2
              AND p.from_date = %(from_date)s
              AND p.to_date = %(to_date)s
              AND p.capacity_warning LIKE '%%PERSISTENT DEFICIT%%'
              {farm_clause}
            """,
            args,
            as_dict=True,
        )
        if not rows:
            return None
        return _alert(
            "hot",
            _plural(len(rows), "shift", "shifts") + " carrying deficit for 3+ weeks",
            "Pump capacity, coverage or the shift schedule needs review — this is not "
            "recovering on its own.",
            route=f"/app/irrigation-planner/{rows[0]['name']}",
            count=len(rows),
        )

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

    # ── Alert: scheduler health ─────────────────────────────────
    def scheduler_health():
        cfg = frappe.db.get_value(
            "Irrigation Scheduler",
            "Irrigation Scheduler",
            ["enabled", "last_run_status", "last_run_summary", "last_run_at", "auto_run_enabled"],
            as_dict=True,
        )
        if not cfg:
            return None
        if not cfg.get("enabled"):
            return _alert(
                "clay",
                "Scheduler is disabled",
                "No weekly planners will be generated until it is enabled.",
                route="/app/irrigation-scheduler",
            )
        if cfg.get("last_run_status") in ("Partial", "Failed", "Aborted"):
            return _alert(
                "hot",
                f"Last scheduler run: {cfg['last_run_status']}",
                cfg.get("last_run_summary") or "Check the run log for errors.",
                route="/app/irrigation-scheduler-run",
            )
        if cfg.get("enabled") and not cfg.get("auto_run_enabled"):
            return _alert(
                "warn",
                "Auto-run is off",
                "The Friday cron will exit without generating planners; runs must be "
                "triggered manually.",
                route="/app/irrigation-scheduler",
            )
        return None

    # ── This week's schedule, as a section × day grid ───────────
    def schedule_grid():
        """Every scheduled shift this week, bucketed by section and weekday.

        This is the shape api.scheduler.assign_daily_blocks actually produces —
        shift i lands on day floor(i*7/N) — so the grid is the schedule rather
        than a picture of it.
        """
        rows = frappe.db.sql(
            f"""
            SELECT
                p.name              AS planner,
                p.farm              AS farm,
                p.block             AS shift,
                p.shift_hours       AS hours,
                p.scheduled_start   AS start_dt,
                p.scheduled_end     AS end_dt,
                p.capacity_warning  AS warning,
                p.total_water_needed_mm AS needed,
                p.delivered_depth_mm    AS delivered,
                p.cycles_count      AS cycles
            FROM `tabIrrigation Planner` p
            WHERE p.docstatus < 2
              AND p.from_date = %(from_date)s
              AND p.to_date = %(to_date)s
              {farm_clause}
            ORDER BY p.scheduled_start ASC, p.block ASC
            """,
            args,
            as_dict=True,
        )
        if not rows:
            return None

        now_dt = frappe.utils.now_datetime()
        start_date = frappe.utils.getdate(from_date)
        sections = {}

        for r in rows:
            section = (r["shift"] or "").split(" - SHIFT ")[0] or "?"
            bucket = sections.setdefault(
                section,
                {"section": section, "farm": r["farm"], "days": [[] for _ in range(7)],
                 "needed": 0.0, "delivered": 0.0},
            )
            bucket["needed"] += float(r["needed"] or 0)
            bucket["delivered"] += float(r["delivered"] or 0)

            day_index = None
            if r["start_dt"]:
                day_index = (frappe.utils.getdate(r["start_dt"]) - start_date).days
            if day_index is None or day_index < 0 or day_index > 6:
                # Unscheduled or out-of-window: park it on the first day rather
                # than dropping it, so the grid still totals correctly.
                day_index = 0

            hours = float(r["hours"] or 0)
            warning = r["warning"] or ""
            if hours <= 0:
                state = "dry"
            elif r["start_dt"] and r["end_dt"] and r["start_dt"] <= now_dt <= r["end_dt"]:
                state = "running"
            elif "Pump capacity capped" in warning:
                state = "capped"
            else:
                state = "scheduled"

            bucket["days"][day_index].append({
                "planner": r["planner"],
                "shift": r["shift"],
                "hours": round(hours, 1),
                "cycles": int(r["cycles"] or 0),
                "state": state,
                "starts_at": str(r["start_dt"]) if r["start_dt"] else None,
            })

        day_labels = []
        today = frappe.utils.getdate(frappe.utils.nowdate())
        for i in range(7):
            d = frappe.utils.getdate(frappe.utils.add_days(start_date, i))
            # Built by hand: strftime("%-d") is glibc-only.
            day_labels.append({
                "date": str(d),
                "label": f"{_DAY_NAMES[d.weekday()][:3]} {d.day}",
                "today": d == today,
            })

        ordered = sorted(sections.values(), key=lambda s: s["section"])
        for s in ordered:
            s["coverage_pct"] = (
                round(100.0 * s["delivered"] / s["needed"], 1) if s["needed"] > 0 else None
            )
            s["needed"] = round(s["needed"], 1)
            s["delivered"] = round(s["delivered"], 1)

        return {"days": day_labels, "sections": ordered}

    grid = _guard("schedule grid", schedule_grid)

    # ── Deficit carried, recent weeks ───────────────────────────
    def deficit_trend(weeks=12):
        rows = frappe.db.sql(
            f"""
            SELECT p.to_date AS to_date,
                   COALESCE(SUM(p.unmet_deficit_mm), 0) AS unmet
            FROM `tabIrrigation Planner` p
            WHERE p.docstatus < 2
              AND p.to_date <= %(to_date)s
              {farm_clause}
            GROUP BY p.to_date
            ORDER BY p.to_date DESC
            LIMIT %(weeks)s
            """,
            dict(args, weeks=weeks),
            as_dict=True,
        )
        if not rows:
            return None
        rows.reverse()
        return {
            "labels": [str(r["to_date"]) for r in rows],
            "values": [round(float(r["unmet"] or 0), 1) for r in rows],
        }

    trend = _guard("deficit trend", deficit_trend)

    for label, fn in (
        ("pump capped", pump_capped),
        ("chronic deficit", chronic),
        ("persistent deficit", persistent),
        ("disease risk", disease_risk),
        ("missing readings", missing_readings),
        ("stale sensors", stale_sensors),
        ("scheduler health", scheduler_health),
    ):
        found = _guard(label, fn)
        if found:
            alerts.append(found)

    severity_rank = {"hot": 0, "warn": 1, "clay": 2, "ok": 3}
    alerts.sort(key=lambda a: severity_rank.get(a["severity"], 9))

    return {
        "week": {
            "from_date": from_date,
            "to_date": to_date,
            "week_starts_on": week_start,
            "planners": int(totals.get("planners") or 0),
            "farms": int(totals.get("farms") or 0),
            "shift_hours": round(float(totals.get("hours") or 0), 1),
            "rainfall_mm": round(float(totals.get("rainfall") or 0), 1),
            "et_crop_mm": round(float(totals.get("et_crop") or 0), 1),
        },
        "tiles": tiles,
        "alerts": alerts,
        "schedule": grid,
        "deficit_trend": trend,
        "generated_at": frappe.utils.get_datetime_str(frappe.utils.now_datetime()),
        "farm_filter": farm or "all",
    }
