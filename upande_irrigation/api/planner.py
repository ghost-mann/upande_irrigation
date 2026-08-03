"""Planning & Scheduling API — the read model behind the Planning view.

Four questions, one payload:

  · when does each shift actually run this week?          → `gantt`
  · how hard is each pump working, day by day?            → `pump_load`
  · is the farm keeping up with what it loses?            → `balance`
  · where did one shift's hours come from?                → `explain`

The planner stores only the first and last moment of a run plus the cycle count
and length, so the individual cycle windows are reconstructed here the same way
api.scheduler.live_sections does — from scheduled_start, cycles_count,
cycle_hours_each and cycle_rest_hours. Both readers therefore draw the same
picture, which they would not if each invented its own spacing.
"""

import datetime

import frappe

from upande_irrigation.api.scheduler import plan_window, pump_groups
from upande_irrigation.events.irrigation_planner import settings_dict

#: Weeks of history in the balance chart. Twelve reads as a season without
#: compressing the bars into hairlines.
_BALANCE_WEEKS = 12

#: Weeks offered in the picker.
_WEEK_CHOICES = 26

_DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _section_of(shift):
	return shift.split(" - SHIFT ")[0] if shift and " - SHIFT " in shift else (shift or "—")


def _short(section):
	s = str(section or "").replace("_SECTION", "").strip()
	return f"{s[:-2]} Ha" if s.endswith("HA") else (s or "—")


def _guard(farm):
	if not farm:
		return "", {}
	return " AND p.farm = %(farm)s", {"farm": farm}


def cycle_windows(start, cycles, hours_each, rest_hours):
	"""Rebuild a run's individual cycle windows.

	Mirrors how the allocator laid them out: equal cycles separated by
	cycle_rest_hours. Returns [] when the shift was never scheduled.
	"""
	if not start or not cycles or hours_each <= 0:
		return []
	out = []
	cursor = frappe.utils.get_datetime(start)
	for i in range(int(cycles)):
		end = cursor + datetime.timedelta(hours=hours_each)
		out.append((cursor, end))
		cursor = end + datetime.timedelta(hours=rest_hours)
	return out


def _planners(week_from, week_to, farm):
	clause, args = _guard(farm)
	args.update({"f": str(week_from), "t": str(week_to)})
	return frappe.db.sql(
		f"""
		SELECT p.name, p.farm, p.block AS shift, p.from_date, p.to_date,
		       p.required_hours, p.shift_hours, p.cycles_count, p.cycle_hours_each,
		       p.scheduled_start, p.scheduled_end,
		       p.total_water_needed_mm, p.delivered_depth_mm, p.unmet_deficit_mm,
		       p.carried_deficit_mm, p.this_week_deficit, p.capacity_warning,
		       p.applied_rate_mm_hr, p.applied_coverage_pct
		FROM `tabIrrigation Planner` p
		WHERE p.docstatus < 2 AND p.from_date = %(f)s AND p.to_date = %(t)s {clause}
		ORDER BY SUBSTRING_INDEX(p.block, ' - SHIFT ', 1),
		         CAST(SUBSTRING_INDEX(p.block, ' - SHIFT ', -1) AS UNSIGNED)
		""",
		args,
		as_dict=True,
	)


@frappe.whitelist()
def fetch(farm=None, week=None):
	farm = (farm or "").strip()
	cfg = frappe.get_cached_doc("Irrigation Scheduler")
	settings = settings_dict()
	rest = settings["cycle_rest_hours"]

	# ── Which weeks can be looked at ─────────────────────────────
	clause, args = _guard(farm)
	weeks = frappe.db.sql(
		f"""
		SELECT p.from_date, p.to_date, COUNT(*) AS planners,
		       ROUND(SUM(p.shift_hours), 1) AS hours
		FROM `tabIrrigation Planner` p
		WHERE p.docstatus < 2 {clause}
		GROUP BY p.from_date, p.to_date
		ORDER BY p.from_date DESC
		LIMIT {_WEEK_CHOICES}
		""",
		args,
		as_dict=True,
	)

	default_from, default_to = plan_window(cfg)
	chosen_from = frappe.utils.getdate(week) if week else None
	if chosen_from is None:
		# The configured week if it has planners, else the most recent that does —
		# opening on a deliberately blank week would look like a broken view.
		if any(str(w["from_date"]) == str(default_from) for w in weeks):
			chosen_from = default_from
		elif weeks:
			chosen_from = frappe.utils.getdate(weeks[0]["from_date"])
		else:
			chosen_from = default_from
	chosen_to = frappe.utils.add_days(chosen_from, 6)

	rows = _planners(chosen_from, chosen_to, farm)

	# ── Gantt: one row per shift, positioned by real clock time ──
	week_start = datetime.datetime.combine(chosen_from, datetime.time.min)
	span_hours = 7 * 24.0
	today = frappe.utils.getdate(frappe.utils.nowdate())
	now_dt = frappe.utils.now_datetime()

	days = []
	for i in range(7):
		d = frappe.utils.add_days(chosen_from, i)
		days.append({
			"date": str(d),
			"label": f"{_DAY_LABELS[frappe.utils.getdate(d).weekday()]} {frappe.utils.getdate(d).day}",
			"today": str(d) == str(today),
		})

	sections = {}
	for r in rows:
		sec = _section_of(r["shift"])
		bucket = sections.setdefault(sec, {"section": sec, "label": _short(sec), "shifts": []})
		windows = cycle_windows(
			r["scheduled_start"], r["cycles_count"], float(r["cycle_hours_each"] or 0), rest
		)
		bars = []
		for start, end in windows:
			left = (start - week_start).total_seconds() / 3600.0
			width = (end - start).total_seconds() / 3600.0
			if width <= 0 or left < 0 or left >= span_hours:
				continue
			state = "done" if end <= now_dt else ("running" if start <= now_dt < end else "future")
			bars.append({
				"left_pct": round(100.0 * left / span_hours, 4),
				"width_pct": round(100.0 * min(width, span_hours - left) / span_hours, 4),
				"hours": round(width, 2),
				"start": str(start),
				"end": str(end),
				"state": state,
			})
		required = float(r["required_hours"] or 0)
		granted = float(r["shift_hours"] or 0)
		bucket["shifts"].append({
			"planner": r["name"],
			"shift": r["shift"],
			"n": r["shift"].split(" - SHIFT ")[-1] if " - SHIFT " in r["shift"] else r["shift"],
			"required_hours": round(required, 2),
			"granted_hours": round(granted, 2),
			"met_pct": round(100.0 * granted / required, 1) if required > 0 else None,
			"bars": bars,
			"unscheduled": required > 0 and not bars,
			"warning": r["capacity_warning"] or "",
		})

	# ── Pump load: hours per pump per day against capacity ───────
	owner = {}
	for f in {r["farm"] for r in rows}:
		for pump_name, prefixes in pump_groups(f).items():
			for p in prefixes:
				owner[(f, p)] = pump_name

	pumps = {}
	for r in rows:
		pump = owner.get((r["farm"], _section_of(r["shift"]))) or "Unassigned pump"
		bucket = pumps.setdefault(pump, {"pump": pump, "days": [0.0] * 7, "total": 0.0, "shifts": 0})
		bucket["shifts"] += 1
		for start, end in cycle_windows(
			r["scheduled_start"], r["cycles_count"], float(r["cycle_hours_each"] or 0), rest
		):
			# A cycle can straddle midnight; charge each day the hours it actually ran.
			cursor = start
			while cursor < end:
				day_index = (cursor.date() - chosen_from).days
				midnight = datetime.datetime.combine(
					cursor.date() + datetime.timedelta(days=1), datetime.time.min
				)
				slice_end = min(end, midnight)
				if 0 <= day_index < 7:
					hours = (slice_end - cursor).total_seconds() / 3600.0
					bucket["days"][day_index] += hours
					bucket["total"] += hours
				cursor = slice_end

	pump_rows = []
	for name, b in sorted(pumps.items(), key=lambda kv: -kv[1]["total"]):
		pump_rows.append({
			"pump": name,
			"days": [round(h, 2) for h in b["days"]],
			"total": round(b["total"], 2),
			"shifts": b["shifts"],
			# 24 h is the ceiling a single day can physically offer.
			"peak_day_pct": round(100.0 * max(b["days"]) / 24.0, 1) if b["days"] else 0.0,
		})

	# ── Balance: demand vs delivered vs carried, recent weeks ────
	# Millimetres are a DEPTH, so they do not add across shifts — 55 shifts each
	# owed 89 mm is not 4,900 mm of anything. Averaging keeps the figure a depth and
	# comparable week to week; a true farm total would need each shift's irrigated
	# area, which the model does not yet hold.
	clause, args = _guard(farm)
	balance = frappe.db.sql(
		f"""
		SELECT p.from_date, COUNT(*) AS shifts,
		       ROUND(AVG(p.total_water_needed_mm), 1) AS demand,
		       ROUND(AVG(p.delivered_depth_mm), 1)    AS delivered,
		       ROUND(AVG(p.unmet_deficit_mm), 1)      AS carried
		FROM `tabIrrigation Planner` p
		WHERE p.docstatus < 2 {clause}
		GROUP BY p.from_date
		ORDER BY p.from_date DESC
		LIMIT {_BALANCE_WEEKS}
		""",
		args,
		as_dict=True,
	)
	balance.reverse()

	# Hours DO add — they are a claim on one pump's week. Depths do not.
	total_required = sum(float(r["required_hours"] or 0) for r in rows)
	total_granted = sum(float(r["shift_hours"] or 0) for r in rows)
	n = len(rows) or 1
	mean_demand = sum(float(r["total_water_needed_mm"] or 0) for r in rows) / n
	mean_delivered = sum(float(r["delivered_depth_mm"] or 0) for r in rows) / n

	return {
		"week": {
			"from_date": str(chosen_from),
			"to_date": str(chosen_to),
			"is_current": str(chosen_from) == str(default_from),
			"planners": len(rows),
			"required_hours": round(total_required, 1),
			"granted_hours": round(total_granted, 1),
			"met_pct": round(100.0 * total_granted / total_required, 1) if total_required > 0 else None,
			"demand_mm_per_shift": round(mean_demand, 1),
			"delivered_mm_per_shift": round(mean_delivered, 1),
			"unscheduled": sum(
				1 for s in sections.values() for sh in s["shifts"] if sh["unscheduled"]
			),
		},
		"weeks": [
			{
				"from_date": str(w["from_date"]),
				"to_date": str(w["to_date"]),
				"planners": int(w["planners"] or 0),
				"hours": float(w["hours"] or 0),
			}
			for w in weeks
		],
		"days": days,
		"gantt": {"sections": [sections[k] for k in sorted(sections)]},
		"pump_load": {"pumps": pump_rows},
		"balance": {
			"unit": "mm per shift",
			"labels": [str(b["from_date"]) for b in balance],
			"shifts": [int(b["shifts"] or 0) for b in balance],
			"demand": [float(b["demand"] or 0) for b in balance],
			"delivered": [float(b["delivered"] or 0) for b in balance],
			"carried": [float(b["carried"] or 0) for b in balance],
		},
		"farm_filter": farm or "all",
		"generated_at": str(now_dt),
	}


@frappe.whitelist()
def explain(planner):
	"""Every step from the measured week to this shift's granted hours.

	Reads the figures the engine recorded rather than recomputing them, so the
	explainer cannot drift from what the planner actually says.
	"""
	planner = (planner or "").strip()
	if not planner:
		frappe.throw("A planner is required.")

	p = frappe.db.get_value(
		"Irrigation Planner",
		planner,
		[
			"name", "farm", "block", "from_date", "to_date",
			"measured_from", "measured_to", "weather_completeness",
			"weekly_rainfall_mm", "weekly_et_pan_mm", "raw_et_crop_mm",
			"et_crop_coefficient", "clean_et_crop_mm", "this_week_deficit",
			"carried_deficit_mm", "total_water_needed_mm", "required_hours",
			"applied_rate_mm_hr", "applied_coverage_pct",
			"shift_hours", "cycles_count", "cycle_hours_each",
			"scheduled_start", "scheduled_end", "delivered_depth_mm",
			"unmet_deficit_mm", "capacity_warning", "z_value", "z_risk_level",
		],
		as_dict=True,
	)
	if not p:
		frappe.throw(f"No Irrigation Planner named {planner}.")

	rate = float(p["applied_rate_mm_hr"] or 0)
	coverage = float(p["applied_coverage_pct"] or 0)
	required = float(p["required_hours"] or 0)
	granted = float(p["shift_hours"] or 0)

	steps = [
		{
			"key": "measure",
			"label": "Measured week",
			"detail": f"{p['measured_from']} → {p['measured_to']} · {p['weather_completeness'] or '—'}",
			"value": None,
			"unit": "",
			"note": "The seven days that had already happened when this plan was made.",
		},
		{
			"key": "et_crop",
			"label": "ET crop, summed",
			"detail": "from the daily Weather Readings",
			"value": round(float(p["raw_et_crop_mm"] or 0), 2),
			"unit": "mm",
			"note": f"Rainfall over the same week: {round(float(p['weekly_rainfall_mm'] or 0), 2)} mm.",
		},
		{
			"key": "clean",
			"label": "Clean ET crop",
			"detail": f"{round(float(p['raw_et_crop_mm'] or 0), 2)} × {p['et_crop_coefficient']} crop coefficient",
			"value": round(float(p["clean_et_crop_mm"] or 0), 2),
			"unit": "mm",
			"note": "What the canopy actually transpired, not what the pan lost.",
		},
		{
			"key": "deficit",
			"label": "This week's deficit",
			"detail": f"{round(float(p['clean_et_crop_mm'] or 0), 2)} − {round(float(p['weekly_rainfall_mm'] or 0), 2)} mm rain, floored at zero",
			"value": round(float(p["this_week_deficit"] or 0), 2),
			"unit": "mm",
			"note": "Rain above demand does not bank credit.",
		},
		{
			"key": "carried",
			"label": "Carried in",
			"detail": "unmet millimetres from this shift's last planner",
			"value": round(float(p["carried_deficit_mm"] or 0), 2),
			"unit": "mm",
			"note": "Debt follows the shift, not the section.",
		},
		{
			"key": "total",
			"label": "Total needed",
			"detail": "this week's deficit plus what was carried in",
			"value": round(float(p["total_water_needed_mm"] or 0), 2),
			"unit": "mm",
			"note": "",
		},
		{
			"key": "required",
			"label": "Hours required",
			"detail": (
				f"{round(float(p['total_water_needed_mm'] or 0), 2)} ÷ {rate} mm/hr "
				f"÷ {coverage}% coverage"
			),
			"value": round(required, 2),
			"unit": "hr",
			"note": "Dividing by coverage lengthens the run: only part of the ground is wetted.",
		},
		{
			"key": "granted",
			"label": "Hours granted",
			"detail": p["capacity_warning"] or "full demand met — the pump had room",
			"value": round(granted, 2),
			"unit": "hr",
			"note": (
				f"{round(100.0 * granted / required, 1)}% of what the week needed."
				if required > 0
				else ""
			),
		},
		{
			"key": "cycles",
			"label": "Split into cycles",
			"detail": (
				f"{p['cycles_count']} × {round(float(p['cycle_hours_each'] or 0), 2)} hr"
				if p["cycles_count"]
				else "not scheduled"
			),
			"value": int(p["cycles_count"] or 0),
			"unit": "cycles",
			"note": (
				f"{p['scheduled_start']} → {p['scheduled_end']}"
				if p["scheduled_start"]
				else "No window — the pump queue filled the week first."
			),
		},
		{
			"key": "delivered",
			"label": "Water delivered",
			"detail": f"{round(granted, 2)} hr × {rate} mm/hr × {coverage}%",
			"value": round(float(p["delivered_depth_mm"] or 0), 2),
			"unit": "mm",
			"note": "",
		},
		{
			"key": "unmet",
			"label": "Carried out",
			"detail": "total needed minus delivered",
			"value": round(float(p["unmet_deficit_mm"] or 0), 2),
			"unit": "mm",
			"note": "Opens next week's demand for this shift.",
		},
	]

	return {
		"planner": p["name"],
		"farm": p["farm"],
		"shift": p["block"],
		"section": _section_of(p["block"]),
		"week": {"from_date": str(p["from_date"]), "to_date": str(p["to_date"])},
		"steps": steps,
		"disease": {"z_value": p["z_value"], "band": p["z_risk_level"]},
		"met_pct": round(100.0 * granted / required, 1) if required > 0 else None,
	}
