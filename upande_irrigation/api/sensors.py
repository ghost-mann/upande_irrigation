"""IoT Sensor Telemetry API.

Reads the `Sensor Readings` doctype (owned by upande_sensors) and shapes it for
the dashboard's IoT view, which works the way an operator asks the question:
pick a site, pick a sensor type, pick a sensor, look at its readings.

So this returns the three filter lists plus a device roster, and a detailed
series for one selected sensor only. Aggregation happens here because the table
is large — roughly half a million rows — and a chart wants a few hundred points,
not tens of thousands.

Was previously the `Fetch Sensor Data` Server Script (api_method =
fetchSensorData).
Callable as: frappe.call({method: 'upande_irrigation.api.sensors.fetch'})
"""

import datetime

import frappe

# The dashboard's own guard, reused rather than reinvented: it logs the failure
# to the Error Log and returns a default so one unavailable doctype costs its own
# view instead of the whole page.
from upande_irrigation.api.overview import _guard

# A device silent longer than this is stale. Same threshold api/overview.py
# alerts on and the view badges.
STALE_HOURS = 6

# Readings carry an epoch-zero timestamp when the gateway sends no clock. They
# are real rows but carry no usable time, so they are excluded rather than
# dragging every axis back to 1970.
_MIN_VALID = "2000-01-01"

# One sensor on screen can afford a denser line than a whole fleet could.
_TARGET_POINTS = 320


def _bucket_seconds(days):
    """Pick a bucket that lands near _TARGET_POINTS across the window."""
    raw = (max(1, int(days)) * 86400) / _TARGET_POINTS
    for step in (60, 300, 900, 1800, 3600, 10800, 21600, 43200, 86400, 604800):
        if raw <= step:
            return step
    return 604800


def _empty(days, start_dt, end_dt):
    """The payload shape fetch() returns when `Sensor Readings` cannot be read.

    Every key the view reads is present and empty, so the Sensors view renders
    "no data" instead of throwing. `meta.unavailable` lets the view say which of
    the two it is.
    """
    return {
        "meta": {
            "days": days,
            "start": str(start_dt),
            "end": str(end_dt),
            "last_reading_at": None,
            "unavailable": True,
        },
        "selection": {"site_name": "", "sensor_type": "", "deveui": ""},
        "sites": [],
        "types": [],
        "devices": [],
        "series": {"labels": [], "avg": [], "min": [], "max": []},
        "stats": None,
    }


@frappe.whitelist()
def fetch(days=30, site_name="", sensor_type="", deveui="", sensor_name="", limit=None):
    """Guarded entry point.

    `Sensor Readings` is owned by upande_sensors, which is not in every site's
    app inventory. Every query below reads `tabSensor Readings` directly, so on
    a site without that app the first one raises and takes the dashboard with
    it. api/overview.py already anticipated exactly this in _guard's docstring;
    this reuses it rather than inventing a second mechanism.
    """
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, 3650))

    end_dt = frappe.utils.now_datetime()
    start_dt = frappe.utils.add_to_date(end_dt, days=-days)

    return _guard(
        "sensors",
        lambda: _fetch(days, site_name, sensor_type, deveui, start_dt, end_dt),
        default=_empty(days, start_dt, end_dt),
    )


def _fetch(days, site_name, sensor_type, deveui, start_dt, end_dt):
    site_name = (site_name or "").strip()
    sensor_type = (sensor_type or "").strip()
    deveui = (deveui or "").strip()

    args = {"start": start_dt, "end": end_dt, "min_valid": _MIN_VALID}
    window = "r.timestamp >= %(start)s AND r.timestamp <= %(end)s"

    # ── Newest reading overall, window-independent ───────────────
    # Lets the view say "nothing in the last 30 days, newest is 17 days old"
    # instead of rendering a blank screen.
    last_overall = frappe.db.sql(
        "SELECT MAX(r.timestamp) FROM `tabSensor Readings` r WHERE r.timestamp >= %(min_valid)s",
        args,
    )[0][0]

    # ── Filter list: sites ───────────────────────────────────────
    sites = frappe.db.sql(
        f"""
        SELECT COALESCE(r.site_name, '') AS site_name,
               COUNT(DISTINCT r.deveui)  AS devices,
               COUNT(*)                  AS readings
        FROM `tabSensor Readings` r
        WHERE {window}
        GROUP BY r.site_name
        ORDER BY readings DESC
        """,
        args,
        as_dict=True,
    )
    site_names = [s["site_name"] for s in sites if s["site_name"]]
    if site_name and site_name not in site_names:
        site_name = ""

    site_clause = ""
    if site_name:
        site_clause = " AND r.site_name = %(site_name)s"
        args["site_name"] = site_name

    # ── Filter list: types within the chosen site ────────────────
    types = frappe.db.sql(
        f"""
        SELECT r.sensor_type              AS sensor_type,
               COUNT(DISTINCT r.deveui)   AS devices,
               COUNT(*)                   AS readings
        FROM `tabSensor Readings` r
        WHERE {window}{site_clause}
        GROUP BY r.sensor_type
        ORDER BY readings DESC
        """,
        args,
        as_dict=True,
    )
    type_names = [t["sensor_type"] for t in types if t["sensor_type"]]
    if sensor_type and sensor_type not in type_names:
        sensor_type = ""
    if not sensor_type and type_names:
        sensor_type = type_names[0]

    type_clause = ""
    if sensor_type:
        type_clause = " AND r.sensor_type = %(sensor_type)s"
        args["sensor_type"] = sensor_type

    # ── Device roster for the dropdown ───────────────────────────
    # Identity is (device, type): one node reports Temperature and Humidity
    # under a single deveui, and collapsing them loses a whole metric.
    devices = frappe.db.sql(
        f"""
        SELECT
            r.deveui                                     AS deveui,
            MAX(r.sensor_name)                           AS sensor_name,
            r.sensor_type                                AS sensor_type,
            MAX(COALESCE(r.site_name, ''))               AS site_name,
            MAX(r.units)                                 AS units,
            COUNT(*)                                     AS reading_count,
            MAX(r.timestamp)                             AS last_seen,
            TIMESTAMPDIFF(HOUR, MAX(r.timestamp), NOW()) AS hours_silent
        FROM `tabSensor Readings` r
        WHERE {window}{site_clause}{type_clause}
        GROUP BY r.deveui, r.sensor_type
        ORDER BY MAX(r.sensor_name), r.deveui
        """,
        args,
        as_dict=True,
    )

    roster = [
        {
            "deveui": d["deveui"],
            "sensor_name": d["sensor_name"] or d["deveui"],
            "sensor_type": d["sensor_type"],
            "site_name": d["site_name"],
            "units": (d["units"] or "").strip(),
            "reading_count": int(d["reading_count"] or 0),
            "last_seen": str(d["last_seen"]) if d["last_seen"] else None,
            "hours_silent": int(d["hours_silent"] or 0),
            "stale": int(d["hours_silent"] or 0) > STALE_HOURS,
        }
        for d in devices
    ]

    if deveui and not any(d["deveui"] == deveui for d in roster):
        deveui = ""
    if not deveui and roster:
        deveui = roster[0]["deveui"]

    selected = next((d for d in roster if d["deveui"] == deveui), None)

    # ── The selected sensor, in detail ───────────────────────────
    series = {"labels": [], "avg": [], "min": [], "max": []}
    stats = None
    if selected:
        args["deveui"] = deveui
        bucket = _bucket_seconds(days)
        args["bucket"] = bucket

        rows = frappe.db.sql(
            f"""
            SELECT FLOOR(UNIX_TIMESTAMP(r.timestamp) / %(bucket)s) AS slot,
                   AVG(r.value) AS avg_value,
                   MIN(r.value) AS min_value,
                   MAX(r.value) AS max_value
            FROM `tabSensor Readings` r
            WHERE {window}{type_clause} AND r.deveui = %(deveui)s
            GROUP BY slot
            ORDER BY slot ASC
            """,
            args,
            as_dict=True,
        )
        for r in rows:
            ts = datetime.datetime.fromtimestamp(int(r["slot"]) * bucket)
            series["labels"].append(ts.strftime("%Y-%m-%d %H:%M:%S"))
            series["avg"].append(round(float(r["avg_value"]), 3) if r["avg_value"] is not None else None)
            series["min"].append(round(float(r["min_value"]), 3) if r["min_value"] is not None else None)
            series["max"].append(round(float(r["max_value"]), 3) if r["max_value"] is not None else None)

        agg = frappe.db.sql(
            f"""
            SELECT COUNT(*) AS readings, MIN(r.value) AS min_value, MAX(r.value) AS max_value,
                   AVG(r.value) AS avg_value
            FROM `tabSensor Readings` r
            WHERE {window}{type_clause} AND r.deveui = %(deveui)s
            """,
            args,
            as_dict=True,
        )[0]

        latest = frappe.db.sql(
            f"""
            SELECT r.value, r.units, r.timestamp, r.battery, r.rssi, r.snr, r.gateway
            FROM `tabSensor Readings` r
            WHERE {window}{type_clause} AND r.deveui = %(deveui)s
            ORDER BY r.timestamp DESC
            LIMIT 1
            """,
            args,
            as_dict=True,
        )
        last = latest[0] if latest else {}

        stats = {
            "deveui": deveui,
            "sensor_name": selected["sensor_name"],
            "sensor_type": selected["sensor_type"],
            "site_name": selected["site_name"],
            "units": (last.get("units") or selected["units"] or "").strip(),
            "readings": int(agg["readings"] or 0),
            "min_value": round(float(agg["min_value"]), 3) if agg["min_value"] is not None else None,
            "max_value": round(float(agg["max_value"]), 3) if agg["max_value"] is not None else None,
            "avg_value": round(float(agg["avg_value"]), 3) if agg["avg_value"] is not None else None,
            "latest_value": last.get("value"),
            "latest_at": str(last.get("timestamp")) if last.get("timestamp") else None,
            "battery": last.get("battery"),
            "rssi": last.get("rssi"),
            "snr": last.get("snr"),
            "gateway": last.get("gateway"),
            "stale": selected["stale"],
            "hours_silent": selected["hours_silent"],
            "bucket_seconds": bucket,
            "points": len(series["labels"]),
        }

    return {
        "meta": {
            "days": days,
            "start": str(start_dt),
            "end": str(end_dt),
            "last_reading_at": str(last_overall) if last_overall else None,
        },
        "selection": {"site_name": site_name, "sensor_type": sensor_type, "deveui": deveui},
        "sites": [
            {"site_name": s["site_name"], "devices": int(s["devices"] or 0), "readings": int(s["readings"] or 0)}
            for s in sites
            if s["site_name"]
        ],
        "types": [
            {"sensor_type": t["sensor_type"], "devices": int(t["devices"] or 0), "readings": int(t["readings"] or 0)}
            for t in types
            if t["sensor_type"]
        ],
        "devices": roster,
        "series": series,
        "stats": stats,
    }


# ─────────────────────────────────────────────────────────────────
# Fleet overview — every sensor at once
# ─────────────────────────────────────────────────────────────────
# Meniscus opened on this: one card per device, fleet KPIs and a raw readings
# table. fetch() above answers "tell me about this one sensor"; fleet() answers
# "is everything reporting?", which is the first thing an operator checks.

# Raw rows for the Recent Readings table.
FLEET_READINGS_LIMIT = 200
# Points in each card's sparkline.
_SPARK_POINTS = 24


def _fleet_empty(days, start_dt, end_dt):
    return {
        "meta": {"days": days, "start": str(start_dt), "end": str(end_dt), "unavailable": True},
        "kpis": {"sensors": 0, "readings": 0, "avg_battery": None, "avg_rssi": None, "online": 0},
        "devices": [],
        "readings": [],
    }


@frappe.whitelist()
def fleet(days=30, site_name="", sensor_type=""):
    """Every (device, measurement) in the window with its latest reading, plus
    fleet KPIs and the newest raw rows. Guarded like fetch()."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, 3650))
    end_dt = frappe.utils.now_datetime()
    start_dt = frappe.utils.add_to_date(end_dt, days=-days)

    return _guard(
        "sensors fleet",
        lambda: _fleet(days, (site_name or "").strip(), (sensor_type or "").strip(), start_dt, end_dt),
        default=_fleet_empty(days, start_dt, end_dt),
    )


def _num(v, places=3):
    return round(float(v), places) if v is not None else None


def _fleet(days, site_name, sensor_type, start_dt, end_dt):
    args = {"start": start_dt, "end": end_dt}
    where = "r.timestamp >= %(start)s AND r.timestamp <= %(end)s"
    if site_name:
        where += " AND r.site_name = %(site_name)s"
        args["site_name"] = site_name
    if sensor_type:
        where += " AND r.sensor_type = %(sensor_type)s"
        args["sensor_type"] = sensor_type

    # One row per (device, measurement): same identity rule as fetch()'s roster.
    agg = frappe.db.sql(
        f"""
        SELECT r.deveui, r.sensor_type,
               MAX(r.sensor_name)             AS sensor_name,
               MAX(COALESCE(r.site_name, '')) AS site_name,
               MAX(r.units)                   AS units,
               COUNT(*)                       AS reading_count,
               MIN(r.value)                   AS min_value,
               MAX(r.value)                   AS max_value,
               MAX(r.timestamp)               AS last_seen
        FROM `tabSensor Readings` r
        WHERE {where}
        GROUP BY r.deveui, r.sensor_type
        ORDER BY MAX(r.sensor_name), r.deveui
        """,
        args,
        as_dict=True,
    )

    # The latest row per (device, measurement): value, battery and link quality.
    latest = frappe.db.sql(
        f"""
        SELECT r.deveui, r.sensor_type, r.value, r.battery, r.rssi, r.snr, r.timestamp
        FROM `tabSensor Readings` r
        INNER JOIN (
            SELECT deveui, sensor_type, MAX(timestamp) AS ts
            FROM `tabSensor Readings` r
            WHERE {where}
            GROUP BY deveui, sensor_type
        ) m ON m.deveui = r.deveui AND m.sensor_type = r.sensor_type AND m.ts = r.timestamp
        """,
        args,
        as_dict=True,
    )
    last_by = {(x["deveui"], x["sensor_type"]): x for x in latest}

    bucket = max(60, int(days * 86400 / _SPARK_POINTS))
    args["bucket"] = bucket
    spark_rows = frappe.db.sql(
        f"""
        SELECT r.deveui, r.sensor_type,
               FLOOR(UNIX_TIMESTAMP(r.timestamp) / %(bucket)s) AS slot,
               AVG(r.value) AS v
        FROM `tabSensor Readings` r
        WHERE {where}
        GROUP BY r.deveui, r.sensor_type, slot
        ORDER BY slot ASC
        """,
        args,
        as_dict=True,
    )
    spark_by = {}
    for x in spark_rows:
        spark_by.setdefault((x["deveui"], x["sensor_type"]), []).append(_num(x["v"]))

    now = frappe.utils.now_datetime()
    devices = []
    for a in agg:
        key = (a["deveui"], a["sensor_type"])
        last = last_by.get(key, {})
        seen = a["last_seen"]
        hours = int((now - seen).total_seconds() // 3600) if seen else None
        devices.append({
            "deveui": a["deveui"],
            "sensor_name": a["sensor_name"] or a["deveui"],
            "sensor_type": a["sensor_type"],
            "site_name": a["site_name"],
            "units": (a["units"] or "").strip(),
            "reading_count": int(a["reading_count"] or 0),
            "min_value": _num(a["min_value"]),
            "max_value": _num(a["max_value"]),
            "latest_value": _num(last.get("value")),
            "latest_at": str(seen) if seen else None,
            "hours_silent": hours,
            "stale": hours is None or hours > STALE_HOURS,
            "battery": _num(last.get("battery"), 2),
            "rssi": _num(last.get("rssi"), 1),
            "snr": _num(last.get("snr"), 1),
            "spark": spark_by.get(key, []),
        })

    readings = frappe.db.sql(
        f"""
        SELECT r.timestamp, r.sensor_name, r.deveui, r.sensor_type, r.value, r.units,
               r.battery, r.rssi, r.snr
        FROM `tabSensor Readings` r
        WHERE {where}
        ORDER BY r.timestamp DESC
        LIMIT {FLEET_READINGS_LIMIT}
        """,
        args,
        as_dict=True,
    )

    # Physical devices, not (device, measurement) pairs: one node reporting
    # temperature and humidity is one sensor to the person counting them.
    by_device = {}
    for d in devices:
        by_device.setdefault(d["deveui"], d)
    batteries = [d["battery"] for d in by_device.values() if d["battery"] is not None]
    rssis = [d["rssi"] for d in by_device.values() if d["rssi"] is not None]

    return {
        "meta": {"days": days, "start": str(start_dt), "end": str(end_dt), "unavailable": False,
                 "stale_hours": STALE_HOURS},
        "kpis": {
            "sensors": len(by_device),
            "readings": sum(d["reading_count"] for d in devices),
            "avg_battery": round(sum(batteries) / len(batteries), 2) if batteries else None,
            "avg_rssi": round(sum(rssis) / len(rssis), 1) if rssis else None,
            "online": len({d["deveui"] for d in devices if not d["stale"]}),
        },
        "devices": devices,
        "readings": [
            {
                "timestamp": str(r["timestamp"]) if r["timestamp"] else None,
                "sensor_name": r["sensor_name"] or r["deveui"],
                "sensor_type": r["sensor_type"],
                "value": _num(r["value"]),
                "units": (r["units"] or "").strip(),
                "battery": _num(r["battery"], 2),
                "rssi": _num(r["rssi"], 1),
                "snr": _num(r["snr"], 1),
            }
            for r in readings
        ],
    }
