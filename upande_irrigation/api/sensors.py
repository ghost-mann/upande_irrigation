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


@frappe.whitelist()
def fetch(days=30, site_name="", sensor_type="", deveui="", sensor_name="", limit=None):
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, 3650))

    site_name = (site_name or "").strip()
    sensor_type = (sensor_type or "").strip()
    deveui = (deveui or "").strip()

    end_dt = frappe.utils.now_datetime()
    start_dt = frappe.utils.add_to_date(end_dt, days=-days)
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
