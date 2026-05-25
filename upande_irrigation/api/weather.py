"""Weather/Irrometer Dashboard API.

Aggregates Weather Reading + Irrometer Reading for the meniscus dashboard.

Was previously the `Fetch Weather Data` Server Script.
Callable as: frappe.call({method: 'upande_irrigation.api.weather.fetch'})
"""

import frappe


@frappe.whitelist()
def fetch(days=30, farm="", start_date="", end_date=""):
	# ── Settings ─────────────────────────────────────────────────
	settings = frappe.get_cached_doc("Irrigation Settings") if frappe.db.exists("DocType", "Irrigation Settings") else None
	depth_per_cup_mm = float(getattr(settings, "default_depth_per_cup_mm", None) or 0.5)
	kpan             = float(getattr(settings, "default_kpan", None) or 0.75)

	# ── Inputs ───────────────────────────────────────────────────
	try:
		days = int(days)
	except (TypeError, ValueError):
		days = 30
	days = max(1, min(days, 3650))

	today = frappe.utils.nowdate()

	if start_date and end_date and start_date <= end_date:
		pass  # use provided dates
	else:
		start_date = frappe.utils.add_days(today, -(days - 1))
		end_date   = today

	# ── Farms dropdown ───────────────────────────────────────────
	all_farms  = frappe.get_all("Farm", fields=["name"], order_by="name asc")
	farms_list = [{"name": f["name"], "label": f["name"]} for f in all_farms]

	# ── Block → Section (Warehouse tree) ─────────────────────────
	block_section_rows = frappe.db.sql("""
		SELECT b.name AS block, b.parent_warehouse AS section
		FROM `tabWarehouse` b
		INNER JOIN `tabWarehouse` s ON s.name = b.parent_warehouse
		WHERE s.warehouse_type = 'Section'
		  AND COALESCE(b.disabled, 0) = 0
		  AND COALESCE(s.disabled, 0) = 0
	""", as_dict=True)

	block_to_section = {r["block"]: r["section"] for r in block_section_rows}
	all_block_warehouses = list(block_to_section.keys())

	# ── Weather Readings ─────────────────────────────────────────
	wx_filters = {"date": ["between", [start_date, end_date]]}
	if farm:
		wx_filters["farm"] = farm

	weather_rows = frappe.get_all(
		"Weather Reading",
		fields=[
			"name", "date", "farm", "rainfall_mm", "pan_cups", "pan_depth_mm",
			"minimum_temperature", "maximum_temperature", "temperature_average",
			"et_pan", "et_crop", "effective_temp", "cumulative_temperature", "swd",
			"z_value", "z_risk_level",
		],
		filters=wx_filters,
		order_by="date asc",
		limit_page_length=0,
	)

	enriched = []
	total_rain = total_evap = total_eto = total_etcrop = 0.0
	mean_sum   = 0.0
	mean_count = 0
	max_temp_overall = min_temp_overall = None
	wet_days = dry_days = 0

	for r in weather_rows:
		cups         = r.get("pan_cups") or 0
		depth        = r.get("pan_depth_mm") or 0
		rain         = r.get("rainfall_mm") or 0
		tmin         = r.get("minimum_temperature")
		tmax         = r.get("maximum_temperature")
		tavg_stored  = r.get("temperature_average")
		etpan_stored = r.get("et_pan")
		etcrop       = r.get("et_crop") or 0
		swd          = r.get("swd")

		if etpan_stored is not None and etpan_stored > 0:
			daily_evap = etpan_stored
		else:
			daily_evap = max(0, rain + depth)
		eto = daily_evap * kpan

		if tavg_stored and tavg_stored > 0:
			mean_t = tavg_stored
		elif tmin is not None and tmax is not None:
			mean_t = (tmin + tmax) / 2.0
		else:
			mean_t = None

		date_str = str(r.get("date")) if r.get("date") is not None else None

		enriched.append({
			"name":                   r.get("name"),
			"date":                   date_str,
			"farm":                   r.get("farm"),
			"rainfall_mm":            rain,
			"pan_cups":               cups,
			"pan_depth_mm":           depth,
			"minimum_temperature":    tmin,
			"maximum_temperature":    tmax,
			"mean_temperature":       round(mean_t, 2) if mean_t is not None else None,
			"daily_evaporation":      round(daily_evap, 2),
			"eto":                    round(eto, 2),
			"et_crop":                round(etcrop, 2) if etcrop else 0,
			"swd":                    swd,
			"cumulative_temperature": r.get("cumulative_temperature"),
			"z_value":                r.get("z_value"),
			"z_risk_level":           r.get("z_risk_level"),
		})

		total_rain   += rain
		total_evap   += daily_evap
		total_eto    += eto
		total_etcrop += etcrop

		if mean_t is not None:
			mean_sum   += mean_t
			mean_count += 1
		if tmax is not None:
			max_temp_overall = tmax if max_temp_overall is None else max(max_temp_overall, tmax)
		if tmin is not None:
			min_temp_overall = tmin if min_temp_overall is None else min(min_temp_overall, tmin)
		if rain > 0.1:
			wet_days += 1
		else:
			dry_days += 1

	avg_mean_temp = (mean_sum / mean_count) if mean_count > 0 else 0
	water_deficit = max(0, total_eto - total_rain)
	balance_ratio = (total_rain / total_eto) if total_eto > 0 else 0

	kpis = {
		"total_rainfall":       round(total_rain, 2),
		"total_evaporation":    round(total_evap, 2),
		"total_eto":            round(total_eto, 2),
		"total_et_crop":        round(total_etcrop, 2),
		"avg_mean_temperature": round(avg_mean_temp, 2),
		"water_deficit":        round(water_deficit, 2),
		"max_temperature":      max_temp_overall,
		"min_temperature":      min_temp_overall,
		"wet_days":             wet_days,
		"dry_days":             dry_days,
		"balance_ratio":        round(balance_ratio, 3),
		"reading_count":        len(enriched),
	}

	# ── Irrometer readings ───────────────────────────────────────
	blocks = []
	blocks_error = None
	try:
		if all_block_warehouses:
			irro_rows = frappe.get_all(
				"Irrometer Reading",
				fields=["irrigation_block", "irrometer_1ft_reading", "irrometer_2ft_reading", "date"],
				filters=[["irrigation_block", "in", all_block_warehouses]],
				order_by="date asc",
				limit_page_length=0,
			)
		else:
			irro_rows = []

		sec_data = {}
		for row in irro_rows:
			blk = row.get("irrigation_block")
			if not blk:
				continue
			sec_data.setdefault(blk, []).append({
				"date": str(row.get("date")) if row.get("date") else None,
				"ft1":  row.get("irrometer_1ft_reading"),
				"ft2":  row.get("irrometer_2ft_reading"),
			})

		for blk_name, parent_section in block_to_section.items():
			history = sec_data.get(blk_name, [])
			latest  = history[-1] if history else {}
			blocks.append({
				"name":              blk_name,
				"block_name":        blk_name.replace(" - KL", ""),
				"parent_section":    parent_section,
				"irrometer_1ft":     latest.get("ft1"),
				"irrometer_2ft":     latest.get("ft2"),
				"irrometer_date":    latest.get("date"),
				"irrometer_history": history,
				"is_active":         True,
			})

		blocks.sort(key=lambda b: (b["parent_section"], b["block_name"]))

	except Exception as e:
		blocks_error = str(e)

	block_summary = {
		"total_blocks":  len(blocks),
		"active_blocks": len(blocks),
		"total_area_ha": 0,
		"total_trees":   0,
	}

	# ── Response ─────────────────────────────────────────────────
	return {
		"meta": {
			"days":             days,
			"start_date":       str(start_date),
			"end_date":         str(end_date),
			"depth_per_cup_mm": depth_per_cup_mm,
			"kpan":             kpan,
			"farm":             farm or "all",
		},
		"farms":         farms_list,
		"kpis":          kpis,
		"weather":       enriched,
		"blocks":        blocks,
		"block_summary": block_summary,
		"blocks_error":  blocks_error,
	}
