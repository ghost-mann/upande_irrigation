"""Before Save handler for Irrigation Planner.

Computes per-shift hours, deficits, pump-capacity capping and child sub-block
rows from the weekly Weather Reading aggregate.

Was previously the `Irrigation Planner — Compute Shift` Server Script.
Registered in hooks.py under doc_events["Irrigation Planner"]["before_save"].

Model v3.2:
  - Each shift waters its OWN trees independently; needs the FULL weekly deficit
    (not 1/N of it).
  - Pump is shared across shifts → per-shift hours capped at WEEK_HOURS / N.
  - WEEK_HOURS = water_target_m3_per_week / pump_flow_rate_m3_per_hr from
    Irrigation Pump Profile (per farm + section). Falls back to 168 hr/wk +
    warning when no profile exists.
  - Unmet deficit carries forward per-shift (per zone).
"""

import frappe

CHRONIC_DEFICIT_THRESHOLD_MM = 50.0
CHRONIC_WEEKS_THRESHOLD = 3


def compute_shift(doc, method=None):
	settings = frappe.get_doc("Irrigation Settings")
	default_avocado_coeff = float(settings.default_avocado_utilisation or 0.65)
	default_coverage      = float(settings.default_irrigation_coverage or 70.0)
	default_mm_hr         = float(settings.default_application_rate_mm_hr or 2.8)
	default_density       = int(settings.default_plant_density or 250)        # noqa: F841
	default_micro_output  = float(settings.default_micro_output or 70.0)      # noqa: F841

	# ── Required fields ──────────────────────────────────────────
	if not doc.farm:
		frappe.throw("Farm is required.")
	if not doc.from_date or not doc.to_date:
		frappe.throw("From Date and To Date are required.")
	if not doc.block:
		frappe.throw("Block (shift) is required.")

	from_date_obj = frappe.utils.getdate(doc.from_date)
	to_date_obj   = frappe.utils.getdate(doc.to_date)
	if from_date_obj > to_date_obj:
		frappe.throw("To Date cannot be before From Date.")

	days = frappe.utils.date_diff(to_date_obj, from_date_obj) + 1
	if days != 7:
		expected_to = frappe.utils.add_days(from_date_obj, 6)
		frappe.throw(f"A week must be exactly 7 days. Set To Date to {expected_to}.")

	from_date_str = str(doc.from_date)
	to_date_str   = str(doc.to_date)

	# ── Identify section ─────────────────────────────────────────
	shift_name = doc.block
	if " - SHIFT " not in shift_name:
		frappe.throw(f"Block must follow the pattern '{{SECTION}} - SHIFT {{N}}'. Got: {shift_name}")
	section_prefix = shift_name.split(" - SHIFT ")[0]

	# ── Week labels ──────────────────────────────────────────────
	week_row = frappe.db.sql("""
		SELECT
			DATE_FORMAT(%s, '%%e %%b')     AS from_label,
			DATE_FORMAT(%s, '%%e %%b %%Y') AS to_label,
			WEEK(%s, 3)                    AS iso_week
	""", (from_date_str, to_date_str, to_date_str), as_dict=True)
	doc.week_dates      = f"{section_prefix} | {week_row[0]['from_label']} - {week_row[0]['to_label']}"
	doc.irrigation_week = week_row[0]["iso_week"]

	# ── Weather aggregation, filtered by farm ────────────────────
	agg = frappe.db.sql("""
		SELECT
			COUNT(*)                            AS day_count,
			COALESCE(SUM(rainfall_mm), 0)       AS weekly_rainfall,
			COALESCE(SUM(et_pan), 0)            AS weekly_et_pan,
			COALESCE(SUM(et_crop), 0)           AS weekly_et_crop,
			AVG(CASE WHEN minimum_temperature > 0 AND maximum_temperature > 0
			         THEN (minimum_temperature + maximum_temperature) / 2.0
			         ELSE NULL END)             AS mean_weekly_temp,
			COUNT(CASE WHEN minimum_temperature > 0 AND maximum_temperature > 0
			           THEN 1 ELSE NULL END)    AS temp_days
		FROM `tabWeather Reading`
		WHERE date BETWEEN %s AND %s
		  AND farm = %s
	""", (from_date_str, to_date_str, doc.farm), as_dict=True)

	day_count        = int(agg[0]["day_count"] or 0)
	weekly_rainfall  = float(agg[0]["weekly_rainfall"] or 0)
	weekly_et_pan    = float(agg[0]["weekly_et_pan"]   or 0)
	weekly_et_crop   = float(agg[0]["weekly_et_crop"]  or 0)
	mean_weekly_temp = float(agg[0]["mean_weekly_temp"] or 0)
	temp_days        = int(agg[0]["temp_days"] or 0)

	doc.weekly_rainfall_mm   = round(weekly_rainfall, 2)
	doc.weekly_et_pan_mm     = round(weekly_et_pan, 2)
	doc.raw_et_crop_mm       = round(weekly_et_crop, 4)
	doc.weather_completeness = f"{day_count} of 7 days logged"

	# ── Apply Kc → this week's deficit ───────────────────────────
	et_coeff = float(doc.et_crop_coefficient or default_avocado_coeff)
	doc.et_crop_coefficient = et_coeff

	clean_et_crop = round(weekly_et_crop * et_coeff, 4)
	doc.clean_et_crop_mm = clean_et_crop

	this_week_deficit = max(0.0, clean_et_crop - weekly_rainfall)
	doc.irrigation_deficit_mm = round(this_week_deficit, 4)

	# ── Per-shift carry-forward from prev week ───────────────────
	prev_to_date = frappe.utils.add_days(from_date_obj, -1)
	prev_planner = frappe.db.sql("""
		SELECT name, unmet_deficit_mm
		FROM `tabIrrigation Planner`
		WHERE farm = %s
		  AND block = %s
		  AND to_date = %s
		ORDER BY creation DESC
		LIMIT 1
	""", (doc.farm, doc.block, str(prev_to_date)), as_dict=True)

	carried_deficit = float(prev_planner[0]["unmet_deficit_mm"] or 0) if prev_planner else 0.0
	doc.carried_deficit_mm = round(carried_deficit, 2)

	# ── Total deficit this shift's zone must address ─────────────
	total_deficit = this_week_deficit + carried_deficit
	no_irrigation = (total_deficit == 0.0)
	doc.total_water_needed_mm = round(total_deficit, 4)

	# ── Z value (anthracnose) ────────────────────────────────────
	if temp_days > 0 and mean_weekly_temp > 0:
		z_val = round(-58.99 + (3.22 * mean_weekly_temp) + (0.18 * weekly_rainfall), 2)
		doc.z_value = z_val
		if z_val >= 20:
			doc.z_risk_level = "High Risk - Fungicide Required"
		elif z_val >= 15:
			doc.z_risk_level = "Infection Risk - Monitor Closely"
		elif z_val >= 5:
			doc.z_risk_level = "Spore Release - Low Alert"
		else:
			doc.z_risk_level = "Low Risk"
	else:
		doc.z_value = 0.0
		doc.z_risk_level = "Insufficient Data"

	# ── Active shifts in this section/farm ───────────────────────
	active_shifts = frappe.db.sql("""
		SELECT name FROM `tabBlock Type`
		WHERE name LIKE %s AND is_active = 1 AND farm = %s
	""", (section_prefix + " - SHIFT %", doc.farm), as_dict=True)
	active_shift_count = len(active_shifts)
	doc.active_shift_count = active_shift_count

	# ── Pump capacity lookup (v3.2) ──────────────────────────────
	# Pump Profile links via (farm, irrigation_section). Section warehouse name
	# follows pattern "<PREFIX>_SECTION", e.g. "23HA_SECTION".
	# WEEK_HOURS = water_target_m3_per_week / pump_flow_rate_m3_per_hr.
	WEEK_HOURS  = 168.0
	pump_source = "no Pump Profile — using 168 hr/wk fallback"
	pump_row = frappe.db.sql("""
		SELECT pp.name AS profile,
		       pp.water_target_m3_per_week AS target_m3,
		       pp.pump_flow_rate_m3_per_hr AS flow_m3hr
		FROM `tabIrrigation Pump Profile` pp
		INNER JOIN `tabWarehouse` w ON w.name = pp.irrigation_section
		WHERE pp.farm = %s
		  AND w.warehouse_type = 'Section'
		  AND w.custom_farm = %s
		  AND w.warehouse_name LIKE %s
		LIMIT 1
	""", (doc.farm, doc.farm, section_prefix + "_SECTION%"), as_dict=True)

	if pump_row:
		target = float(pump_row[0]["target_m3"] or 0)
		flow   = float(pump_row[0]["flow_m3hr"] or 0)
		if target > 0 and flow > 0:
			WEEK_HOURS  = round(target / flow, 2)
			pump_source = f"{pump_row[0]['profile']} ({WEEK_HOURS} hr/wk)"
		else:
			pump_source = f"{pump_row[0]['profile']} — target or flow missing; using 168 hr/wk fallback"

	mm_hr    = default_mm_hr
	coverage = default_coverage

	if no_irrigation or active_shift_count == 0 or mm_hr <= 0 or coverage <= 0:
		per_shift_needed = 0.0
		per_shift_max    = 0.0
		per_shift_actual = 0.0
		delivered_depth  = 0.0
	else:
		per_shift_needed = total_deficit / mm_hr / (coverage / 100.0)
		per_shift_max    = WEEK_HOURS / active_shift_count
		per_shift_actual = min(per_shift_needed, per_shift_max)
		delivered_depth  = per_shift_actual * mm_hr * (coverage / 100.0)

	shift_hours        = round(per_shift_actual, 2)
	delivered_depth_mm = round(delivered_depth, 2)
	unmet_deficit      = max(0.0, total_deficit - delivered_depth)

	doc.shift_hours        = shift_hours
	doc.delivered_depth_mm = delivered_depth_mm
	doc.unmet_deficit_mm   = round(unmet_deficit, 2)

	# ── Warnings ─────────────────────────────────────────────────
	warnings = []

	if no_irrigation:
		doc.no_irrigation_reason = (
			f"Rainfall {round(weekly_rainfall, 1)} mm meets or exceeds "
			f"Clean ET Crop {round(clean_et_crop, 1)} mm and no carryover. "
			"No irrigation required."
		)
	else:
		doc.no_irrigation_reason = ""

	if per_shift_needed > per_shift_max and per_shift_max > 0:
		over_mm = round((per_shift_needed - per_shift_max) * mm_hr * (coverage / 100.0), 2)
		warnings.append(
			f"Pump capacity capped ({pump_source}): this shift needs "
			f"{round(per_shift_needed, 2)} hrs but only {round(per_shift_max, 2)} "
			f"hrs available (pump shared with {active_shift_count - 1} other shifts). "
			f"{over_mm} mm carries to next week."
		)
	elif "fallback" in pump_source:
		warnings.append(
			f"Pump capacity unverified: {pump_source}. "
			f"Create Irrigation Pump Profile for section {section_prefix} "
			"to enable accurate pump capping."
		)

	if unmet_deficit > CHRONIC_DEFICIT_THRESHOLD_MM:
		warnings.append(
			f"CHRONIC DEFICIT: {round(unmet_deficit, 1)} mm unmet — exceeds "
			f"{CHRONIC_DEFICIT_THRESHOLD_MM} mm threshold. Trees may be stressed."
		)

	# Chronic persistence: how many consecutive past weeks did this shift carry deficit?
	chronic_persistence = 0
	if carried_deficit > 0:
		look_back_to = frappe.utils.add_days(from_date_obj, -1)
		for i in range(CHRONIC_WEEKS_THRESHOLD):
			check_to = frappe.utils.add_days(look_back_to, -7 * i)
			rec = frappe.db.sql("""
				SELECT unmet_deficit_mm
				FROM `tabIrrigation Planner`
				WHERE farm = %s AND block = %s AND to_date = %s
				LIMIT 1
			""", (doc.farm, doc.block, str(check_to)), as_dict=True)
			if rec and float(rec[0]["unmet_deficit_mm"] or 0) > 0:
				chronic_persistence += 1
			else:
				break

	if chronic_persistence >= CHRONIC_WEEKS_THRESHOLD:
		warnings.append(
			f"PERSISTENT DEFICIT: this shift has carried deficit forward for "
			f"{chronic_persistence}+ weeks. Review pump capacity, coverage, or schedule."
		)

	doc.capacity_warning = " | ".join(warnings)

	# ── scheduled_end ────────────────────────────────────────────
	if doc.scheduled_start and shift_hours > 0:
		start_dt = frappe.utils.get_datetime(doc.scheduled_start)
		doc.scheduled_end = frappe.utils.add_to_date(start_dt, hours=shift_hours)
	elif not shift_hours:
		doc.scheduled_end = doc.scheduled_start

	# ── Rebuild irrigation_calculations child rows ───────────────
	saved_overrides = {}
	for row in (doc.irrigation_calculations or []):
		if row.irrigation_block:
			saved_overrides[row.irrigation_block] = {
				"coverage_pct": float(row.irrigation_coverage_pct) if row.irrigation_coverage_pct else None,
				"mm_hr":        float(row.mm_hr)                   if row.mm_hr                   else None,
			}

	sub_blocks = frappe.db.sql("""
		SELECT bl.`block` AS block_name
		FROM `tabBlocks List` bl
		WHERE bl.parent = %s
		ORDER BY bl.idx ASC
	""", (shift_name,), as_dict=True)

	doc.irrigation_calculations = []
	for sub in sub_blocks:
		block_name = sub["block_name"]
		override = saved_overrides.get(block_name, {})

		coverage_pct = override.get("coverage_pct")
		if coverage_pct is None:
			coverage_pct = default_coverage

		rate = override.get("mm_hr")
		if rate is None:
			rate = default_mm_hr

		doc.append("irrigation_calculations", {
			"irrigation_block":        block_name,
			"irrigation_coverage_pct": coverage_pct,
			"mm_hr":                   round(rate, 4),
		})
