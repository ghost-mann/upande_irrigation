"""Water & Energy Dashboard API.

Aggregates Water Meter Reading, Electricity Meter Reading, and
Reservoir Pumping Record for the meniscus dashboard.

Was previously the `Fetch Resource Data API` Server Script
(api_method = fetchResourceData).
Callable as: frappe.call({method: 'upande_irrigation.api.resources.fetch'})
"""

import frappe


def _friendly_label(wh_name):
	"""'23HA_SECTION' -> '23 Ha'; 'PLOT_SECTION' -> 'PLOT'."""
	base = (wh_name or "").replace("_SECTION", "").strip()
	if base.endswith("HA"):
		return base[:-2] + " Ha"
	return base


@frappe.whitelist()
def fetch(days=30, section=None, farm=None):
	# ── Inputs ───────────────────────────────────────────────────
	try:
		days = int(days)
	except (TypeError, ValueError):
		days = 30
	days = max(1, min(days, 3650))

	today      = frappe.utils.nowdate()
	start_date = frappe.utils.add_days(today, -(days - 1))

	# ── 1. Sections (Warehouse tree) ─────────────────────────────
	section_filters = {"warehouse_type": "Section", "disabled": 0}
	if farm:
		section_filters["custom_farm"] = farm

	sections_raw = frappe.get_all(
		"Warehouse",
		fields=["name", "warehouse_name", "custom_farm"],
		filters=section_filters,
		order_by="warehouse_name asc",
		limit_page_length=0,
	)
	sections = [
		{
			"id":   s["name"],
			"name": _friendly_label(s["warehouse_name"]),
			"farm": s.get("custom_farm"),
		}
		for s in sections_raw
	]

	# ── 1b. Irrigation Pump Profile targets ──────────────────────
	# Keyed by irrigation_section so the dashboard can render
	# threshold lines on each section's chart.
	pp_filters = {}
	if farm:
		pp_filters["farm"] = farm
	profile_rows = frappe.get_all(
		"Irrigation Pump Profile",
		fields=[
			"name", "farm", "irrigation_section", "pump_name",
			"pump_flow_rate_m3_per_hr", "pump_kwh_per_hr",
			"water_target_m3_per_week", "kwh_target_per_week",
		],
		filters=pp_filters,
		limit_page_length=0,
	)
	profiles = {}
	for p in profile_rows:
		sec = p.get("irrigation_section")
		if not sec:
			continue
		# Last profile wins if multiple — should normally be one per section
		profiles[sec] = {
			"name":                       p["name"],
			"farm":                       p.get("farm"),
			"pump_name":                  p.get("pump_name"),
			"pump_flow_rate_m3_per_hr":   p.get("pump_flow_rate_m3_per_hr") or 0,
			"pump_kwh_per_hr":            p.get("pump_kwh_per_hr") or 0,
			"water_target_m3_per_week":   p.get("water_target_m3_per_week") or 0,
			"kwh_target_per_week":        p.get("kwh_target_per_week") or 0,
		}

	# ── 2. Water Meter Readings ──────────────────────────────────
	wm_filters = {"date": [">=", start_date]}
	if section:
		wm_filters["irrigation_section"] = section

	water_rows = frappe.get_all(
		"Water Meter Reading",
		fields=["name", "date", "irrigation_section", "previous_reading",
		        "new_reading", "units_used"],
		filters=wm_filters,
		order_by="date asc",
		limit_page_length=0,
	)

	water_by_section = {}
	for r in water_rows:
		sec = r.get("irrigation_section") or "Unknown"
		bucket = water_by_section.setdefault(sec, {"total": 0.0, "readings": []})
		used = r.get("units_used") or 0
		bucket["total"] = round(bucket["total"] + used, 2)
		bucket["readings"].append({
			"date":             str(r.get("date")),
			"previous_reading": r.get("previous_reading"),
			"new_reading":      r.get("new_reading"),
			"units_used":       used,
			"section":          sec,
		})

	water_total = round(sum((r.get("units_used") or 0) for r in water_rows), 2)

	water_daily = {}
	for r in water_rows:
		d = str(r.get("date"))[:10]
		water_daily[d] = round(water_daily.get(d, 0) + (r.get("units_used") or 0), 2)
	water_daily_series = [{"date": k, "units": v} for k, v in sorted(water_daily.items())]

	# ── 3. Electricity Meter Readings (filtered by section) ──────
	em_filters = {"date": [">=", start_date]}
	if section:
		em_filters["irrigation_section"] = section

	elec_rows = frappe.get_all(
		"Electricity Meter Reading",
		fields=["name", "date", "irrigation_section",
		        "previous_reading", "new_reading", "units_used"],
		filters=em_filters,
		order_by="date asc",
		limit_page_length=0,
	)

	elec_by_section = {}
	for r in elec_rows:
		sec = r.get("irrigation_section") or "Unknown"
		bucket = elec_by_section.setdefault(sec, {"total": 0.0, "readings": []})
		used = r.get("units_used") or 0
		bucket["total"] = round(bucket["total"] + used, 2)
		bucket["readings"].append({
			"date":             str(r.get("date")),
			"previous_reading": r.get("previous_reading"),
			"new_reading":      r.get("new_reading"),
			"units_used":       used,
			"section":          sec,
		})

	elec_total = round(sum((r.get("units_used") or 0) for r in elec_rows), 2)

	elec_daily = {}
	for r in elec_rows:
		d = str(r.get("date"))[:10]
		elec_daily[d] = round(elec_daily.get(d, 0) + (r.get("units_used") or 0), 2)
	elec_daily_series = [{"date": k, "units": v} for k, v in sorted(elec_daily.items())]

	# ── 4. Reservoir Pumping (dam pump — no per-section split) ──
	pump_rows = frappe.get_all(
		"Reservoir Pumping Record",
		fields=["name", "date", "pump_start_time", "pump_stop_time", "total_hours",
		        "previous_electricity_meter_reading", "new_electricity_meter_reading",
		        "electricity_used_units",
		        "previous_water_meter_reading", "new_water_meter_reading",
		        "volume_of_water_used_m3"],
		filters={"date": [">=", start_date]},
		order_by="date asc",
		limit_page_length=0,
	)
	pump_total_m3  = round(sum((r.get("volume_of_water_used_m3") or 0) for r in pump_rows), 2)
	pump_total_hrs = round(sum((r.get("total_hours") or 0)             for r in pump_rows), 2)
	pump_total_kwh = round(sum((r.get("electricity_used_units") or 0)  for r in pump_rows), 2)

	pump_series = [
		{
			"date":            str(r.get("date")),
			"pump_start_time": str(r.get("pump_start_time") or ""),
			"pump_stop_time":  str(r.get("pump_stop_time") or ""),
			"total_hours":     r.get("total_hours") or 0,
			"elec_prev":       r.get("previous_electricity_meter_reading"),
			"elec_new":        r.get("new_electricity_meter_reading"),
			"elec_used":       r.get("electricity_used_units") or 0,
			"water_prev":      r.get("previous_water_meter_reading"),
			"water_new":       r.get("new_water_meter_reading"),
			"volume_m3":       r.get("volume_of_water_used_m3") or 0,
		}
		for r in pump_rows
	]

	pump_daily     = {}
	pump_daily_hrs = {}
	for r in pump_rows:
		d = str(r.get("date"))[:10]
		pump_daily[d]     = round(pump_daily.get(d, 0)     + (r.get("volume_of_water_used_m3") or 0), 2)
		pump_daily_hrs[d] = round(pump_daily_hrs.get(d, 0) + (r.get("total_hours") or 0), 2)
	pump_daily_series = [
		{"date": k, "volume_m3": pump_daily[k], "hours": pump_daily_hrs[k]}
		for k in sorted(pump_daily.keys())
	]

	# ── 5. Efficiency ────────────────────────────────────────────
	m3_per_kwh = round(pump_total_m3 / pump_total_kwh, 3) if pump_total_kwh > 0 else None

	# ── 6. Response ──────────────────────────────────────────────
	return {
		"meta": {
			"days":       days,
			"start_date": str(start_date),
			"end_date":   str(today),
			"section":    section,
			"farm":       farm,
		},
		"sections": sections,
		"profiles": profiles,
		"water": {
			"total_m3":     water_total,
			"by_section":   water_by_section,
			"daily_series": water_daily_series,
			"all_readings": [r for sec in water_by_section.values() for r in sec["readings"]],
		},
		"electricity": {
			"total_units":  elec_total,
			"by_section":   elec_by_section,
			"daily_series": elec_daily_series,
			"all_readings": [r for sec in elec_by_section.values() for r in sec["readings"]],
		},
		"pumping": {
			"total_m3":     pump_total_m3,
			"total_hours":  pump_total_hrs,
			"total_kwh":    pump_total_kwh,
			"m3_per_kwh":   m3_per_kwh,
			"daily_series": pump_daily_series,
			"all_readings": pump_series,
		},
		"kpis": {
			"water_total_m3":   water_total,
			"elec_total_units": elec_total,
			"pump_total_m3":    pump_total_m3,
			"pump_total_hours": pump_total_hrs,
			"pump_efficiency":  m3_per_kwh,
		},
	}
