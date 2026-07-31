"""IoT Sensor Telemetry API.

Reads the `Sensor Readings` doctype (owned by upande_sensors) and returns it
already shaped for the dashboard's IoT view: a summary per sensor type, one row
per device, and a downsampled series per type.

Aggregation happens here rather than in the browser because the table is large —
roughly half a million rows on Lokitela — and a chart wants a couple of hundred
points per line, not tens of thousands.

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
# are real rows but carry no usable time, so they are excluded from series and
# ranges rather than dragging every axis back to 1970.
_MIN_VALID = "2000-01-01"

# Target points per line. Anything denser is noise at chart width and costs
# transfer for nothing.
_TARGET_POINTS = 180


def _bucket_seconds(days):
    """Pick a bucket that lands near _TARGET_POINTS across the window."""
    span = max(1, int(days)) * 86400
    raw = span / _TARGET_POINTS
    for step in (300, 900, 1800, 3600, 10800, 21600, 43200, 86400, 604800):
        if raw <= step:
            return step
    return 604800


@frappe.whitelist()
def fetch(days=30, deveui="", sensor_type="", sensor_name="", limit=None):
    # ── Inputs ───────────────────────────────────────────────────
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, 3650))

    deveui = (deveui or "").strip()
    sensor_type = (sensor_type or "").strip()
    sensor_name = (sensor_name or "").strip()

    end_dt = frappe.utils.now_datetime()
    start_dt = frappe.utils.add_to_date(end_dt, days=-days)

    # No _MIN_VALID predicate here: the window start is always well past 2000,
    # so it is redundant, and the extra range condition stopped MariaDB using
    # sensor_type_timestamp_idx. Only the window-independent MAX below needs it.
    where = ["r.timestamp >= %(start)s", "r.timestamp <= %(end)s"]
    args = {
        "start": start_dt,
        "end": end_dt,
        "min_valid": _MIN_VALID,
        "stale_hours": STALE_HOURS,
    }
    if deveui:
        where.append("r.deveui = %(deveui)s")
        args["deveui"] = deveui
    if sensor_type:
        where.append("r.sensor_type = %(sensor_type)s")
        args["sensor_type"] = sensor_type
    if sensor_name:
        where.append("r.sensor_name = %(sensor_name)s")
        args["sensor_name"] = sensor_name
    clause = " AND ".join(where)

    # ── When was anything last heard from, ignoring the window? ──
    # Lets the view say "nothing in the last 30 days; newest is 17 days old"
    # instead of rendering an empty screen with no explanation.
    last_overall = frappe.db.sql(
        f"SELECT MAX(r.timestamp) FROM `tabSensor Readings` r WHERE r.timestamp >= %(min_valid)s",
        args,
    )[0][0]

    # ── Tab bar: every type in the window, cheaply ───────────────
    # GROUP BY sensor_type uses sensor_type_timestamp_idx. This runs regardless
    # of which type is selected, so switching tabs never loses the bar.
    type_rows = frappe.db.sql(
        """
        SELECT r.sensor_type                        AS sensor_type,
               COUNT(*)                             AS readings,
               COUNT(DISTINCT r.deveui)             AS devices,
               MAX(r.timestamp)                     AS last_seen,
               MAX(r.units)                         AS units
        FROM `tabSensor Readings` r
        WHERE r.timestamp >= %(start)s AND r.timestamp <= %(end)s
        GROUP BY r.sensor_type
        ORDER BY readings DESC
        """,
        args,
        as_dict=True,
    )
    type_list = [
        {
            "sensor_type": t["sensor_type"] or "Unknown",
            "devices": int(t["devices"] or 0),
            "readings": int(t["readings"] or 0),
            "units": (t["units"] or "").strip(),
            "last_seen": str(t["last_seen"]) if t["last_seen"] else None,
        }
        for t in type_rows
    ]

    # Detail is scoped to one type — 28 devices rather than 64, and the query
    # can use the index. Default to the busiest, which is what an operator opens
    # on anyway.
    if not sensor_type and type_list:
        sensor_type = type_list[0]["sensor_type"]
        where.append("r.sensor_type = %(sensor_type)s")
        args["sensor_type"] = sensor_type
        clause = " AND ".join(where)

    # ── One row per device ───────────────────────────────────────
    devices = frappe.db.sql(
        f"""
        SELECT
            r.deveui                                        AS deveui,
            MAX(r.sensor_name)                              AS sensor_name,
            r.sensor_type                                   AS sensor_type,
            MAX(r.units)                                    AS units,
            COUNT(*)                                        AS reading_count,
            MIN(r.timestamp)                                AS first_seen,
            MAX(r.timestamp)                                AS last_seen,
            MIN(r.value)                                    AS min_value,
            MAX(r.value)                                    AS max_value,
            AVG(r.value)                                    AS avg_value,
            TIMESTAMPDIFF(HOUR, MAX(r.timestamp), NOW())    AS hours_silent
        FROM `tabSensor Readings` r
        WHERE {clause}
        GROUP BY r.deveui, r.sensor_type
        ORDER BY MAX(r.sensor_name), r.deveui
        """,
        args,
        as_dict=True,
    )

    # ── Latest reading per device ────────────────────────────────
    # Joined on the device's own MAX(timestamp) so this is genuinely the newest
    # row. The previous implementation paged the window ascending and truncated,
    # which surfaced the *oldest* rows as "latest".
    latest_rows = frappe.db.sql(
        f"""
        SELECT r.deveui, r.sensor_type, r.value, r.units, r.timestamp, r.battery, r.rssi, r.snr
        FROM `tabSensor Readings` r
        INNER JOIN (
            SELECT deveui, sensor_type, MAX(timestamp) AS ts
            FROM `tabSensor Readings` r
            WHERE {clause}
            GROUP BY deveui, sensor_type
        ) m ON m.deveui = r.deveui AND m.sensor_type = r.sensor_type AND m.ts = r.timestamp
        """,
        args,
        as_dict=True,
    )
    latest = {}
    for row in latest_rows:
        # A device can log twice in the same instant; either is "latest".
        latest.setdefault((row["deveui"], row["sensor_type"]), row)

    bucket = _bucket_seconds(days)
    args["bucket"] = bucket

    # ── Downsampled series per device ────────────────────────────
    series_rows = frappe.db.sql(
        f"""
        SELECT
            r.deveui                                              AS deveui,
            r.sensor_type                                         AS sensor_type,
            FLOOR(UNIX_TIMESTAMP(r.timestamp) / %(bucket)s)        AS slot,
            AVG(r.value)                                          AS value,
            MIN(r.timestamp)                                      AS slot_start
        FROM `tabSensor Readings` r
        WHERE {clause}
        GROUP BY r.deveui, r.sensor_type, slot
        ORDER BY slot ASC
        """,
        args,
        as_dict=True,
    )

    # ── Shape the series onto one shared time axis per sensor type ──
    slots = sorted({int(r["slot"]) for r in series_rows})
    slot_index = {s: i for i, s in enumerate(slots)}
    labels = []
    for slot in slots:
        # frappe.utils.get_datetime cannot parse a unix integer; convert here.
        labels.append(datetime.datetime.fromtimestamp(slot * bucket).strftime("%Y-%m-%d %H:%M:%S"))

    by_device = {}
    for r in series_rows:
        arr = by_device.setdefault((r["deveui"], r["sensor_type"]), [None] * len(slots))
        arr[slot_index[int(r["slot"])]] = round(float(r["value"]), 3) if r["value"] is not None else None

    # ── Assemble devices ─────────────────────────────────────────
    out_devices = []
    for d in devices:
        duid = d["deveui"]
        key = (duid, d["sensor_type"])
        last = latest.get(key) or {}
        hours = int(d["hours_silent"] or 0)
        stale = hours > STALE_HOURS
        out_devices.append({
            "deveui": duid,
            "sensor_name": d["sensor_name"] or duid,
            "sensor_type": d["sensor_type"] or "Unknown",
            "units": (last.get("units") or d["units"] or "").strip(),
            "reading_count": int(d["reading_count"] or 0),
            "first_seen": str(d["first_seen"]) if d["first_seen"] else None,
            "last_seen": str(d["last_seen"]) if d["last_seen"] else None,
            "hours_silent": hours,
            "stale": stale,
            "latest_value": last.get("value"),
            "battery": last.get("battery"),
            "rssi": last.get("rssi"),
            "snr": last.get("snr"),
            "min_value": round(float(d["min_value"]), 3) if d["min_value"] is not None else None,
            "max_value": round(float(d["max_value"]), 3) if d["max_value"] is not None else None,
            "avg_value": round(float(d["avg_value"]), 3) if d["avg_value"] is not None else None,
            "series": by_device.get(key) or [],
        })

    batteries = [d["battery"] for d in out_devices if d["battery"] is not None]
    rssis = [d["rssi"] for d in out_devices if d["rssi"] is not None]

    return {
        "meta": {
            "days": days,
            "start": str(start_dt),
            "end": str(end_dt),
            "bucket_seconds": bucket,
            "points": len(slots),
            "last_reading_at": str(last_overall) if last_overall else None,
            "sensor_type": sensor_type or None,
        },
        "labels": labels,
        "types": type_list,
        "devices": out_devices,
        "kpis": {
            "device_count": len(out_devices),
            "reading_count": sum(d["reading_count"] for d in out_devices),
            "stale_count": sum(1 for d in out_devices if d["stale"]),
            "type_count": len(type_list),
            "avg_battery": round(sum(batteries) / len(batteries), 2) if batteries else None,
            "avg_rssi": round(sum(rssis) / len(rssis), 2) if rssis else None,
            "window_days": days,
        },
        "filters": {
            "days": days,
            "deveui": deveui or None,
            "sensor_type": sensor_type or None,
            "sensor_name": sensor_name or None,
        },
    }
