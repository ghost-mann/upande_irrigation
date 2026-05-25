"""IoT Sensor Telemetry API.

Reads from the `Sensor Readings` doctype and groups readings by device
for the meniscus dashboard's IoT tab.

Was previously the `Fetch Sensor Data` Server Script
(api_method = fetchSensorData).
Callable as: frappe.call({method: 'upande_irrigation.api.sensors.fetch'})
"""

import frappe


@frappe.whitelist()
def fetch(days=7, deveui="", sensor_type="", sensor_name="", limit=5000):
	# ── Inputs ───────────────────────────────────────────────────
	try:
		days = int(days)
	except (TypeError, ValueError):
		days = 7
	try:
		limit = int(limit)
	except (TypeError, ValueError):
		limit = 5000

	days  = max(1, min(days, 3650))
	limit = max(1, min(limit, 20000))

	deveui      = (deveui or "").strip()
	sensor_type = (sensor_type or "").strip()
	sensor_name = (sensor_name or "").strip()

	# ── Date window ──────────────────────────────────────────────
	end_dt   = frappe.utils.now_datetime()
	start_dt = frappe.utils.add_to_date(end_dt, days=-days)

	# ── Filters ──────────────────────────────────────────────────
	filters = {"timestamp": [">=", start_dt]}
	if deveui:
		filters["deveui"]      = deveui
	if sensor_type:
		filters["sensor_type"] = sensor_type
	if sensor_name:
		filters["sensor_name"] = sensor_name

	# ── Pull readings ────────────────────────────────────────────
	readings = frappe.get_all(
		"Sensor Readings",
		filters=filters,
		fields=[
			"name", "sensor_name", "deveui", "sensor_type",
			"value", "units", "timestamp", "date",
			"battery", "rssi", "snr",
		],
		order_by="timestamp asc",
		limit_page_length=limit,
	)

	# ── Group by device ──────────────────────────────────────────
	sensors_map = {}   # deveui -> meta
	series_map  = {}   # deveui -> list of {timestamp, value, battery, rssi, snr}
	latest_map  = {}   # deveui -> latest reading row

	batteries = []
	rssis     = []

	for r in readings:
		duid = r.get("deveui") or "unknown"

		# Stringify datetime fields for JSON / JS Date()
		ts = r.get("timestamp")
		if ts and not isinstance(ts, str):
			ts = str(ts)
		r["timestamp"] = ts

		d = r.get("date")
		if d and not isinstance(d, str):
			d = str(d)
		r["date"] = d

		meta = sensors_map.get(duid)
		if meta is None:
			meta = {
				"deveui":        duid,
				"sensor_name":   r.get("sensor_name"),
				"sensor_type":   r.get("sensor_type"),
				"units":         r.get("units") or "",
				"first_seen":    ts,
				"last_seen":     ts,
				"reading_count": 0,
			}
			sensors_map[duid] = meta
			series_map[duid]  = []

		meta["reading_count"] += 1
		meta["last_seen"]     = ts  # readings are asc, so last write wins

		series_map[duid].append({
			"timestamp": ts,
			"value":     r.get("value"),
			"battery":   r.get("battery"),
			"rssi":      r.get("rssi"),
			"snr":       r.get("snr"),
		})

		latest_map[duid] = r

		if r.get("battery") is not None:
			batteries.append(r.get("battery"))
		if r.get("rssi") is not None:
			rssis.append(r.get("rssi"))

	# ── KPIs ─────────────────────────────────────────────────────
	avg_battery = round(sum(batteries) / len(batteries), 2) if batteries else None
	avg_rssi    = round(sum(rssis)     / len(rssis),     2) if rssis     else None

	# First non-null latest value (matches old behaviour — picks any device)
	latest_value = None
	for duid in sensors_map:
		last = latest_map.get(duid) or {}
		if last.get("value") is not None:
			latest_value = last.get("value")
			break

	kpis = {
		"sensor_count":  len(sensors_map),
		"reading_count": len(readings),
		"avg_battery":   avg_battery,
		"avg_rssi":      avg_rssi,
		"latest_value":  latest_value,
		"window_days":   days,
		"start":         str(start_dt),
		"end":           str(end_dt),
	}

	# ── Response ─────────────────────────────────────────────────
	return {
		"sensors":  list(sensors_map.values()),
		"latest":   latest_map,
		"series":   series_map,
		"readings": readings,
		"kpis":     kpis,
		"filters": {
			"days":        days,
			"deveui":      deveui or None,
			"sensor_type": sensor_type or None,
			"sensor_name": sensor_name or None,
		},
	}
