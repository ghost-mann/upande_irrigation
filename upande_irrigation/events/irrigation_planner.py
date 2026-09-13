"""Before Save handler for Irrigation Planner — demand only.

Answers one question: given the elapsed week's weather and what this shift still
owes, how many hours does it need? It writes no clock and no capped figure,
because a single shift cannot know whether the pump is free. api/scheduler.py owns
that and writes shift_hours, the cycle fields, the windows and capacity_warning.

That split is deliberate. Previously this hook did both, so opening a scheduled
planner and pressing Save recomputed uncapped hours straight over the scheduler's
allocation.

Registered in hooks.py under doc_events["Irrigation Planner"]["before_save"].
"""

import frappe

from upande_irrigation.engine import demand as D

CHRONIC_DEFICIT_THRESHOLD_MM = 50.0
CHRONIC_WEEKS_THRESHOLD = 3


def settings_dict():
	"""Irrigation Settings flattened into the keys the engine expects."""
	s = frappe.get_cached_doc("Irrigation Settings")
	return {
		"et_crop_coefficient": float(s.get("default_avocado_utilisation") or 0.65),
		"default_application_rate_mm_hr": float(s.get("default_application_rate_mm_hr") or 2.8),
		"default_irrigation_coverage": float(s.get("default_irrigation_coverage") or 70.0),
		"min_weather_days": int(s.get("min_weather_days") or D.MEASURE_DAYS),
		"auto_cycle_threshold_hrs": float(s.get("auto_cycle_threshold_hrs") or 5.0),
		"cycles_when_above_threshold": int(s.get("cycles_when_above_threshold") or 2),
		"cycles_when_below_threshold": int(s.get("cycles_when_below_threshold") or 1),
		"cycle_rest_hours": float(s.get("cycle_rest_hours") or 2.0),
		# Stand-in capacity for a section with no Irrigation Pump Profile. Stated as
		# how this estate runs its pumps rather than a bare full week.
		"default_water_target_m3_per_week": float(s.get("default_water_target_m3_per_week") or 0),
		"default_pump_flow_rate_m3_per_hr": float(s.get("default_pump_flow_rate_m3_per_hr") or 0),
	}


def pump_defaults(settings):
	"""The farm-wide stand-in for a missing Irrigation Pump Profile."""
	return {
		"water_target_m3_per_week": settings.get("default_water_target_m3_per_week") or 0,
		"pump_flow_rate_m3_per_hr": settings.get("default_pump_flow_rate_m3_per_hr") or 0,
	}


def shift_settings(settings, block):
	"""Settings with this shift's own rate and coverage overlaid.

	Depth in millimetres is per unit area, so a bigger shift does not need a longer
	run — what actually changes a shift's hours is its emitter output and how much
	of the ground those emitters wet. Those are the two overrides Block Type carries,
	and a blank or zero means inherit the farm default. So by default every shift on
	a farm computes identically, and a shift that genuinely differs can say so
	without a code change.
	"""
	out = dict(settings)
	if not block:
		return out

	from upande_irrigation.shifts import shift_overrides

	row = shift_overrides(block)

	rate = float(row.get("application_rate_mm_hr") or 0)
	coverage = float(row.get("irrigation_coverage") or 0)
	if rate > 0:
		out["default_application_rate_mm_hr"] = rate
	if coverage > 0:
		out["default_irrigation_coverage"] = coverage
	return out


def readings_for(farm, start, end):
	"""Weather Readings for one farm across an inclusive date range."""
	return frappe.get_all(
		"Weather Reading",
		filters={"farm": farm, "date": ["between", [str(start), str(end)]]},
		fields=[
			"rainfall_mm",
			"et_pan",
			"et_crop",
			"minimum_temperature",
			"maximum_temperature",
		],
		limit_page_length=0,
	)


def carried_for(farm, block, from_date):
	"""Unmet deficit from the most recent prior planner for this shift.

	An ordered lookup rather than the old exact-date chain, which required
	to_date = from_date − 1 and so dropped the debt to zero whenever a week was
	skipped.
	"""
	rows = frappe.get_all(
		"Irrigation Planner",
		filters={
			"farm": farm,
			"block": block,
			"to_date": ["<", str(from_date)],
			"docstatus": ["<", 2],
		},
		fields=["unmet_deficit_mm"],
		order_by="to_date desc, creation desc",
		limit=1,
	)
	return float(rows[0].get("unmet_deficit_mm") or 0) if rows else 0.0


def persistent_weeks(farm, block, from_date):
	"""How many consecutive prior weeks this shift ended in debt.

	Walks planners backwards by to_date instead of assuming an unbroken 7-day
	chain, so a gap week no longer reads as "debt cleared".
	"""
	rows = frappe.get_all(
		"Irrigation Planner",
		filters={
			"farm": farm,
			"block": block,
			"to_date": ["<", str(from_date)],
			"docstatus": ["<", 2],
		},
		fields=["unmet_deficit_mm"],
		order_by="to_date desc",
		limit=CHRONIC_WEEKS_THRESHOLD,
	)
	streak = 0
	for r in rows:
		if float(r.get("unmet_deficit_mm") or 0) > 0:
			streak += 1
		else:
			break
	return streak


def compute_shift(doc, method=None):
	if not doc.farm:
		frappe.throw("Farm is required.")
	if not doc.from_date or not doc.to_date:
		frappe.throw("From Date and To Date are required.")
	if not doc.block:
		frappe.throw("Block (shift) is required.")

	from_date = frappe.utils.getdate(doc.from_date)
	to_date = frappe.utils.getdate(doc.to_date)
	if from_date > to_date:
		frappe.throw("To Date cannot be before From Date.")
	if frappe.utils.date_diff(to_date, from_date) + 1 != 7:
		expected = frappe.utils.add_days(from_date, 6)
		frappe.throw(f"A week must be exactly 7 days. Set To Date to {expected}.")

	shift_name = doc.block
	if " - SHIFT " not in shift_name:
		frappe.throw(f"Block must follow the pattern '{{SECTION}} - SHIFT {{N}}'. Got: {shift_name}")
	section_prefix = shift_name.split(" - SHIFT ")[0]

	settings = shift_settings(settings_dict(), doc.block)

	# ── Demand comes from the week that has already happened ─────
	m_start, m_end = D.measured_window(from_date)
	agg = D.aggregate(readings_for(doc.farm, m_start, m_end))

	doc.measured_from = m_start
	doc.measured_to = m_end
	doc.weather_completeness = f"{agg['days']} of {settings['min_weather_days']} days logged"

	doc.weekly_rainfall_mm = round(agg["rainfall_mm"], 2)
	doc.weekly_et_pan_mm = round(agg["et_pan_mm"], 2)
	doc.raw_et_crop_mm = round(agg["et_crop_mm"], 4)

	carried = carried_for(doc.farm, doc.block, from_date)
	doc.carried_deficit_mm = round(carried, 2)

	out = D.demand(agg, carried, settings)
	doc.et_crop_coefficient = settings["et_crop_coefficient"]
	# Record the two figures that turned millimetres into hours, so the number can
	# be audited later without re-deriving which defaults were in force at the time.
	doc.applied_rate_mm_hr = settings["default_application_rate_mm_hr"]
	doc.applied_coverage_pct = settings["default_irrigation_coverage"]
	doc.clean_et_crop_mm = out["clean_et_crop_mm"]
	doc.this_week_deficit = out["week_deficit_mm"]
	doc.total_water_needed_mm = out["total_needed_mm"]
	doc.required_hours = out["required_hours"]
	doc.no_irrigation_reason = out["no_irrigation_reason"]

	# ── Labels ───────────────────────────────────────────────────
	doc.week_dates = (
		f"{section_prefix} | {from_date.strftime('%-d %b')} - {to_date.strftime('%-d %b %Y')}"
	)
	doc.irrigation_week = to_date.isocalendar()[1]

	# ── Anthracnose index over the same measured week ─────────────
	doc.z_value, doc.z_risk_level = D.anthracnose(agg)

	from upande_irrigation.shifts import count_active_shifts

	doc.active_shift_count = count_active_shifts(doc.farm, section_prefix)
