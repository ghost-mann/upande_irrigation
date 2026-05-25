"""Before Save handler for Weather Reading.

Computes pan depth, ET pan, ET crop, GDD (cumulative_temperature),
SWD (Soil Water Deficit), and z_value (anthracnose risk index) from
operator inputs (rainfall, pan_cups, min/max temp).

Was previously the `Weather Reading - Before Save` Server Script.
Registered in hooks.py under doc_events["Weather Reading"]["before_save"].

Note: Weather Reading lives in the frappe app's Irrigation module; we hook
into it from upande_irrigation because all the agronomic logic is owned here.
"""

import frappe


def compute_derived(doc, method=None):
	# ── Load settings ─────────────────────────────────────────────
	settings = frappe.get_doc("Irrigation Settings")
	depth_per_cup   = float(settings.depth_per_cup_mm or 0.5)
	crop_coeff      = float(settings.pan_to_crop_coefficient or 0.95)
	base_temp       = float(settings.avocado_base_temperature or 12.5)
	initial_swd     = float(settings.initial_swd_mm or -500)
	gdd_resets      = int(settings.cumulative_temp_resets_yearly or 1)
	swd_resets      = int(settings.get("swd_resets_yearly") or 1)
	cap_swd_at_zero = int(settings.swd_cap_at_zero or 0)

	pan_cups = frappe.utils.flt(doc.pan_cups)
	rainfall = frappe.utils.flt(doc.rainfall_mm)

	# Backfill flag — when True, skip the negative-et_pan floor.
	is_backfill = bool(doc.flags.get("is_backfill"))

	# ── Validations ──────────────────────────────────────────────
	if not doc.date:
		frappe.throw("Date is required. Please enter today's date.")

	if not doc.farm:
		frappe.throw("Farm is required.")

	if rainfall < 0:
		frappe.throw("Rainfall cannot be negative. Enter 0 if there was no rain.")

	if doc.maximum_temperature and doc.minimum_temperature:
		if frappe.utils.flt(doc.maximum_temperature) < frappe.utils.flt(doc.minimum_temperature):
			frappe.throw("Maximum Temperature cannot be less than Minimum Temperature. Please check your readings.")

	# Duplicate check — scoped per farm
	existing = frappe.get_all(
		"Weather Reading",
		filters={"date": str(doc.date), "farm": doc.farm, "name": ["!=", doc.name or ""]},
		limit=1,
	)
	if existing:
		frappe.throw(
			f"A Weather Reading for {doc.farm} on {doc.date} already exists. "
			"Open the existing record to edit it instead."
		)

	# ── Pan & ET calculations ────────────────────────────────────
	pan_depth        = pan_cups * depth_per_cup
	doc.pan_depth_mm = round(pan_depth, 1)

	raw_et_pan = rainfall + pan_depth

	# For NEW records: warn + floor to 0. For BACKFILL: preserve negatives.
	if raw_et_pan < 0 and not is_backfill:
		frappe.msgprint(
			msg=(
				f"Computed pan evaporation came out negative ({round(raw_et_pan, 2)} mm).<br><br>"
				"This usually means the rain gauge and pan readings don't match.<br>"
				f"Rainfall: {round(rainfall, 1)} mm, "
				f"Pan cups: {round(pan_cups, 1)} (= {round(pan_depth, 1)} mm).<br><br>"
				"Please double-check today's readings. The system will treat today's "
				"evaporation as 0 mm to keep downstream calculations safe."
			),
			title="Pan Evaporation Anomaly",
			indicator="orange",
		)
		doc.et_pan  = 0.0
		doc.et_crop = 0.0
	else:
		doc.et_pan  = round(raw_et_pan, 1)
		doc.et_crop = round(doc.et_pan * crop_coeff, 4)

	# ── Temperature aggregates ───────────────────────────────────
	if doc.minimum_temperature and doc.maximum_temperature:
		doc.temperature_average = round(
			(frappe.utils.flt(doc.minimum_temperature) + frappe.utils.flt(doc.maximum_temperature)) / 2.0, 1
		)

	if doc.temperature_average:
		doc.effective_temp = round(max(0, doc.temperature_average - base_temp), 1)
	else:
		doc.effective_temp = 0.0

	# ── Previous-day lookups for GDD and SWD (per farm) ──────────
	date_obj   = frappe.utils.getdate(doc.date)
	yesterday  = frappe.utils.add_days(doc.date, -1)
	year_start = f"{date_obj.year}-01-01"

	# Cumulative Temperature (GDD)
	gdd_window_start = year_start if gdd_resets else "1900-01-01"
	prev_gdd = frappe.get_all(
		"Weather Reading",
		filters={
			"date": ["between", [gdd_window_start, yesterday]],
			"farm": doc.farm,
			"name": ["!=", doc.name or ""],
		},
		fields=["cumulative_temperature"],
		order_by="date desc",
		limit=1,
	)
	prev_cumulative = frappe.utils.flt(prev_gdd[0].cumulative_temperature) if prev_gdd else 0.0
	doc.cumulative_temperature = round(prev_cumulative + doc.effective_temp, 1)

	# Soil Water Deficit (SWD)
	swd_window_start = year_start if swd_resets else "1900-01-01"
	prev_swd_row = frappe.get_all(
		"Weather Reading",
		filters={
			"date": ["between", [swd_window_start, yesterday]],
			"farm": doc.farm,
			"name": ["!=", doc.name or ""],
		},
		fields=["swd"],
		order_by="date desc",
		limit=1,
	)
	prev_swd = frappe.utils.flt(prev_swd_row[0].swd) if prev_swd_row else initial_swd

	daily_balance = rainfall - doc.et_crop
	new_swd       = prev_swd + daily_balance
	if cap_swd_at_zero and new_swd > 0:
		doc.swd = 0.0
	else:
		doc.swd = round(new_swd, 4)

	# ── Z-value (rolling 7-day rain, scoped per farm) ────────────
	if doc.temperature_average and doc.temperature_average > 0:
		week_start = frappe.utils.add_days(date_obj, -6)
		rain_prev = frappe.db.sql("""
			SELECT COALESCE(SUM(rainfall_mm), 0) AS total
			FROM `tabWeather Reading`
			WHERE date BETWEEN %s AND %s
			  AND farm = %s
			  AND name != %s
		""", (week_start, frappe.utils.add_days(doc.date, -1), doc.farm, doc.name or ""))[0][0]
		week_rain = float(rain_prev) + rainfall

		z = -58.99 + 3.22 * doc.temperature_average + 0.18 * week_rain
		doc.z_value = round(z, 2)

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
