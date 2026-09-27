"""The daily Irrigation Run Sheet — generation, operator status, and the views.

generate(farm, date):
  1. bring the block balance up to yesterday (api/balance.rebuild);
  2. per shift, the driest member block decides Due / Ahead and the hours
     (engine/runsheet.shift_need);
  3. per pump, place cycles in that day's run windows (engine/runsheet.place);
  4. write the farm's Run Sheet for the date, keeping every row the operator has
     already acted on (Running, Done, Partial, Skipped) and never re-planning a
     shift that has one.

A section whose pump has no Irrigation Pump Profile uses the settings' default run
window, and all such sections share one queue: an unknown pump might be one pump, so
running them concurrently could over-draw it. Every row carries the reason.
"""

import datetime

import frappe

from upande_irrigation.api import balance as BA
from upande_irrigation.engine import runsheet as R

KEEP = ("Running", "Done", "Partial", "Skipped")
DEFAULT_PUMP = "Default pump (no Pump Profile)"
FULL_REBUILD_GAP_DAYS = 14


def _settings():
	s = frappe.get_cached_doc("Irrigation Settings")
	f = lambda v, d: float(v) if v not in (None, "") else d  # noqa: E731
	return {
		"min_run_hours": f(s.get("min_run_hours"), 0.5),
		"max_run_hours_per_day": f(s.get("max_run_hours_per_day"), 12.0),
		"irrigate_ahead_days": int(s.get("irrigate_ahead_days") or 0),
		"auto_cycle_threshold_hrs": f(s.get("auto_cycle_threshold_hrs"), 5.0),
		"cycles_when_below_threshold": int(s.get("cycles_when_below_threshold") or 1),
		"cycles_when_above_threshold": int(s.get("cycles_when_above_threshold") or 2),
		"cycle_rest_hours": f(s.get("cycle_rest_hours"), 2.0),
		"default_window_start": s.get("default_window_start") or "06:00:00",
		"default_window_end": s.get("default_window_end") or "18:00:00",
	}


def pumps(farm):
	"""{section warehouse: {pump, flow, windows(rows), rest_dates}} from Pump Profiles."""
	out = {}
	for p in frappe.get_all("Irrigation Pump Profile", filters={"farm": farm},
	                        fields=["name", "pump_name", "irrigation_section", "pump_flow_rate_m3_per_hr", "rest_dates"]):
		windows = frappe.get_all("Irrigation Run Window", filters={"parent": p.name, "parenttype": "Irrigation Pump Profile"},
		                         fields=["day", "start_time", "end_time"], order_by="idx asc")
		out[p.irrigation_section] = {"pump": p.pump_name or p.name, "flow": float(p.pump_flow_rate_m3_per_hr or 0),
		                             "windows": windows, "rest_dates": p.rest_dates or ""}
	return out


def _ensure_balance(farm, date):
	"""Balance up to the day before `date`: a short partial rebuild when yesterday's
	stored state is there, else a full one from the origin."""
	yesterday = frappe.utils.add_days(date, -1)
	start = frappe.utils.add_days(date, -FULL_REBUILD_GAP_DAYS)
	have = frappe.db.count("Irrigation Block Balance", {"farm": farm, "date": frappe.utils.add_days(start, -1)})
	if have and frappe.utils.getdate(start) > BA.balance_start():
		BA.rebuild(farm=farm, start=start, end=yesterday)
	else:
		BA.rebuild(farm=farm, end=yesterday)


def plan(farm, date):
	"""The requests and rows for (farm, date), without saving anything."""
	st = BA.state(farm, as_of=date)
	members = BA.shift_members()
	cfg = _settings()
	pump_of = pumps(farm)
	requests, windows, flows = {}, {}, {}
	for shift, blocks in members.items():
		mine = [st[b] for b in blocks if b in st]
		if not mine:
			continue
		need = R.shift_need(mine, cfg)
		if not need["kind"] or not need["hours"]:
			continue
		section = mine[0]["section"]
		prof = pump_of.get(section)
		reasons = list(need["reasons"])
		if prof:
			pump = prof["pump"]
			windows[pump] = R.windows_for_date(date, prof["windows"], prof["rest_dates"])
			flow = sum(float(m.get("block_flow_m3_hr") or 0) for m in mine)
			if prof["flow"] and flow > prof["flow"]:
				reasons.append(f"shift emitter flow {flow:.0f} m³/h exceeds the pump's {prof['flow']:.0f} m³/h")
		else:
			pump = DEFAULT_PUMP
			windows[pump] = R.windows_for_date(date, [{"day": "Every day", "start_time": cfg["default_window_start"],
			                                           "end_time": cfg["default_window_end"]}])
			reasons.append("no Pump Profile for this section — default run window, one shared queue")
		if any(m["stale_weather"] for m in mine):
			reasons.append("weather is estimated — no reading for 3+ days")
		defaulted = sorted({f for m in mine for f in m["using_defaults"] if f in ("soil_texture", "planting_year", "emitter_flow_lph")})
		if defaulted:
			reasons.append("using farm defaults for " + ", ".join(d.replace("_", " ") for d in defaulted))
		requests.setdefault(pump, []).append({"shift": shift, "section": section, "hours": need["hours"],
		                                      "net_mm": need["net_mm"], "urgency": need["urgency"],
		                                      "kind": need["kind"], "reasons": reasons})
	return requests, windows, cfg


@frappe.whitelist()
def generate(farm=None, date=None):
	"""Create or refresh the farm's run sheet for `date` (default today)."""
	frappe.only_for(("System Manager", "Agriculture Manager", "Irrigation User"))
	date = frappe.utils.getdate(date) if date else frappe.utils.getdate()
	farms = [farm] if farm else _balance_farms()
	out = []
	for f in farms:
		_ensure_balance(f, date)
		name = frappe.db.get_value("Irrigation Run Sheet", {"farm": f, "date": date})
		sheet = frappe.get_doc("Irrigation Run Sheet", name) if name else frappe.new_doc("Irrigation Run Sheet")
		if not name:
			sheet.farm, sheet.date, sheet.status = f, date, "Issued"
		kept = [r for r in sheet.get("runs") or [] if r.status in KEEP]
		acted = {r.shift for r in kept}
		requests, windows, cfg = plan(f, date)
		for pump in list(requests):
			requests[pump] = [r for r in requests[pump] if r["shift"] not in acted]
		rows = R.place(requests, windows, cfg)
		sheet.set("runs", [])
		for r in kept:
			sheet.append("runs", r.as_dict())
		for r in sorted(rows, key=lambda x: (x["pump"], x["planned_start"] or datetime.datetime.max, x["shift"])):
			sheet.append("runs", r)
		planned = [r for r in rows if r["status"] == "Planned"]
		unplaced = [r for r in rows if r["status"] == "Not placed"]
		sheet.generated_at = frappe.utils.now_datetime()
		sheet.summary = (
			f"{len({r['shift'] for r in planned})} shifts in {len(planned)} cycles, "
			f"{sum(r['planned_hours'] for r in planned):.1f} h planned"
			+ (f"; {len(unplaced)} not placed ({sum(r['planned_hours'] for r in unplaced):.1f} h)" if unplaced else "")
			+ (f"; {len(kept)} already acted on kept" if kept else "")
		)
		sheet.save(ignore_permissions=True)
		out.append({"farm": f, "run_sheet": sheet.name, "summary": sheet.summary})
	frappe.db.commit()
	return out


def _balance_farms():
	return sorted({p.farm for p in frappe.get_all("Irrigation Block Profile", filters={"is_active": 1}, fields=["farm"]) if p.farm})


@frappe.whitelist(methods=["POST"])
def set_status(run, status, actual_hours=None, skip_reason=None, notes=None):
	"""The operator's tick on one cycle: Running, Done, Partial (actual hours) or Skipped (reason)."""
	frappe.only_for(("System Manager", "Agriculture Manager", "Irrigation User"))
	if status not in ("Planned", "Running", "Done", "Partial", "Skipped"):
		frappe.throw(f"Unknown status {status!r}.")
	parent = frappe.db.get_value("Irrigation Run", run, "parent")
	if not parent:
		raise frappe.DoesNotExistError(f"Run {run} not found")
	sheet = frappe.get_doc("Irrigation Run Sheet", parent)
	row = next(r for r in sheet.runs if r.name == run)
	now = frappe.utils.now_datetime()
	if status == "Partial" and not frappe.utils.flt(actual_hours):
		frappe.throw("Enter the hours actually run for a partial cycle.")
	if status == "Skipped" and not (skip_reason or "").strip():
		frappe.throw("Give a reason for skipping.")
	row.status = status
	if status == "Running":
		row.actual_start = now
	elif status in ("Done", "Partial"):
		row.actual_start = row.actual_start or row.planned_start or now
		row.actual_end = now if status == "Partial" or not row.planned_end else row.planned_end
		row.actual_hours = frappe.utils.flt(actual_hours) or row.planned_hours
	elif status == "Skipped":
		row.skip_reason = skip_reason.strip()
	elif status == "Planned":
		row.actual_start = row.actual_end = None
		row.actual_hours = 0
	if notes is not None:
		row.notes = notes
	sheet.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ok": True, "run": run, "status": row.status}


def _sheet_rows(farm, date):
	name = frappe.db.get_value("Irrigation Run Sheet", {"farm": farm, "date": date})
	if not name:
		return None, []
	sheet = frappe.get_doc("Irrigation Run Sheet", name)
	return sheet, [r.as_dict() for r in sheet.runs]


@frappe.whitelist()
def today(farm=None, date=None):
	"""The run sheet(s) for a date, with each shift's blocks and their water state."""
	date = frappe.utils.getdate(date) if date else frappe.utils.getdate()
	farms = [farm] if farm else _balance_farms()
	members = BA.shift_members()
	out = []
	for f in farms:
		sheet, rows = _sheet_rows(f, date)
		st = BA.state(f, as_of=date)
		for r in rows:
			r["blocks"] = [{"block": b, "label": st[b]["label"], "depletion_pct": st[b]["depletion_pct"],
			                "raw_mm": st[b]["raw_mm"], "depletion_mm": st[b]["depletion_mm"]}
			               for b in members.get(r["shift"], []) if b in st]
		out.append({
			"farm": f, "date": str(date), "run_sheet": sheet.name if sheet else None,
			"status": sheet.status if sheet else None, "summary": sheet.summary if sheet else None,
			"generated_at": str(sheet.generated_at) if sheet and sheet.generated_at else None,
			"runs": rows,
		})
	return {"date": str(date), "sheets": out}


@frappe.whitelist()
def week(farm=None, date=None):
	"""7-day outlook per shift: the day(s) it comes due and the hours, assuming no
	rain and that each due run refills the driest block — plus pump load per day."""
	date = frappe.utils.getdate(date) if date else frappe.utils.getdate()
	farms = [farm] if farm else _balance_farms()
	members = BA.shift_members()
	cfg = _settings()
	days = [str(frappe.utils.add_days(date, k)) for k in range(7)]
	out = []
	for f in farms:
		st = BA.state(f, as_of=date)
		pump_of = pumps(f)
		shifts = []
		load = {}
		for shift, blocks in sorted(members.items()):
			mine = [st[b] for b in blocks if b in st]
			if not mine:
				continue
			cells = []
			ds = {m["block"]: float(m["depletion_mm"]) for m in mine}
			for k in range(7):
				for m in mine:
					ds[m["block"]] += float(m["mean_etc_mm"] or 0)
				worst = max(mine, key=lambda m: ds[m["block"]] / m["raw_mm"] if m["raw_mm"] else 0)
				due = worst["raw_mm"] and ds[worst["block"]] >= worst["raw_mm"]
				hours = 0.0
				if due:
					from upande_irrigation.engine.balance import hours_to_refill

					hours = min(hours_to_refill(ds[worst["block"]], worst["rate_mm_hr"], worst["application_efficiency"]),
					            cfg["max_run_hours_per_day"])
					for m in mine:
						ds[m["block"]] = max(0.0, ds[m["block"]] - hours * float(m["rate_mm_hr"] or 0) * float(m["application_efficiency"] or 0.9))
				cells.append({"date": days[k], "due": bool(due), "hours": round(hours, 1),
				              "depletion_pct": round(100 * ds[worst["block"]] / worst["taw_mm"], 0) if worst["taw_mm"] else 0})
			section = mine[0]["section"]
			pump = (pump_of.get(section) or {}).get("pump") or DEFAULT_PUMP
			for c in cells:
				load.setdefault(pump, {}).setdefault(c["date"], 0.0)
				load[pump][c["date"]] += c["hours"]
			shifts.append({"shift": shift, "section": section, "pump": pump, "cells": cells,
			               "depletion_pct": max(m["depletion_pct"] for m in mine)})
		capacity = {}
		for pump in load:
			prof = next((p for p in pump_of.values() if p["pump"] == pump), None)
			for d in days:
				dd = frappe.utils.getdate(d)
				rows = prof["windows"] if prof else [{"day": "Every day", "start_time": cfg["default_window_start"], "end_time": cfg["default_window_end"]}]
				w = R.windows_for_date(dd, rows, prof["rest_dates"] if prof else "")
				capacity.setdefault(pump, {})[d] = round(sum((b - a).total_seconds() for a, b in w) / 3600.0, 1)
		out.append({"farm": f, "days": days, "shifts": shifts, "load": load, "capacity": capacity})
	return {"date": str(date), "farms": out}


@frappe.whitelist()
def live_sections(farm=None):
	"""Irrigation Now: per section, the cycle running now and the next few, from
	today's run sheet. Same shape the Now view has always rendered."""
	now = frappe.utils.now_datetime()
	date = now.date()
	out = []
	for f in ([farm] if farm else _balance_farms()):
		_sheet, rows = _sheet_rows(f, date)
		by_section = {}
		for r in rows:
			if r["status"] not in ("Planned", "Running") or not r.get("planned_start"):
				continue
			by_section.setdefault(r.get("section") or "—", []).append(r)
		sections = []
		for section, rs in sorted(by_section.items()):
			rs.sort(key=lambda r: r["planned_start"])
			current = None
			for r in rs:
				start, end = frappe.utils.get_datetime(r["planned_start"]), frappe.utils.get_datetime(r["planned_end"])
				if r["status"] == "Running" or start <= now <= end:
					same = [x for x in rs if x["shift"] == r["shift"]]
					current = {
						"shift": r["shift"], "started_at": str(r.get("actual_start") or start), "ends_at": str(end),
						"cycles_count": r.get("cycles") or 1, "cycle_hours_each": r.get("planned_hours") or 0,
						"cycles": [{"n": x["cycle_no"], "starts_at": str(x["planned_start"]), "ends_at": str(x["planned_end"])} for x in same],
						"run": r["name"],
					}
					break
			upcoming = [
				{"shift": r["shift"], "starts_at": str(r["planned_start"]), "shift_hours": r["planned_hours"],
				 "cycles_count": r.get("cycles") or 1, "cycle_hours_each": r["planned_hours"]}
				for r in rs if frappe.utils.get_datetime(r["planned_start"]) > now
			][:3]
			sections.append({"section": section, "current": current, "upcoming": upcoming})
		if sections:
			out.append({"farm": f, "sections": sections})
	return {"farms": out, "now": str(now)}


@frappe.whitelist()
def budget(farm=None, weeks=8):
	"""Per section per week (Monday start): water the crop needed (ETc over the
	block areas), water recorded as applied (gross, from the balance), and water
	metered (Water Meter Reading), with the recorded-vs-metered gap."""
	weeks = max(1, min(int(weeks or 8), 52))
	today = frappe.utils.getdate()
	start = frappe.utils.add_days(today, -today.weekday() - 7 * (weeks - 1))
	args = {"start": start, "farm": farm}
	farm_clause = " AND b.farm = %(farm)s" if farm else ""
	# mm over ha → m³: 1 mm on 1 ha = 10 m³. Recorded net ÷ efficiency = gross applied.
	rows = frappe.db.sql(
		f"""
		SELECT w.parent_warehouse AS section, b.farm AS farm,
		       DATE_SUB(b.date, INTERVAL WEEKDAY(b.date) DAY) AS week,
		       SUM(b.etc_mm * p.area_ha * 10) AS needed_m3,
		       SUM(b.effective_rain_mm * p.area_ha * 10) AS rain_m3,
		       SUM(b.irrigation_mm / IF(p.application_efficiency > 0, p.application_efficiency, 0.9) * p.area_ha * 10) AS recorded_m3
		FROM `tabIrrigation Block Balance` b
		JOIN `tabIrrigation Block Profile` p ON p.name = b.block
		JOIN `tabWarehouse` w ON w.name = b.block
		WHERE b.date >= %(start)s {farm_clause}
		GROUP BY section, b.farm, week
		ORDER BY section, week
		""",
		args,
		as_dict=True,
	)
	metered = frappe.db.sql(
		"""
		SELECT irrigation_section AS section, DATE_SUB(DATE(date), INTERVAL WEEKDAY(date) DAY) AS week,
		       SUM(units_used) AS m3
		FROM `tabWater Meter Reading`
		WHERE docstatus = 1 AND date >= %(start)s
		GROUP BY section, week
		""",
		args,
		as_dict=True,
	)
	meter = {(m.section, str(m.week)): float(m.m3 or 0) for m in metered}
	out = []
	for r in rows:
		m3 = meter.get((r.section, str(r.week)))
		rec = float(r.recorded_m3 or 0)
		out.append({
			"section": r.section, "farm": r.farm, "week": str(r.week),
			"needed_m3": round(float(r.needed_m3 or 0), 0), "rain_m3": round(float(r.rain_m3 or 0), 0),
			"recorded_m3": round(rec, 0), "metered_m3": round(m3, 0) if m3 is not None else None,
			"gap_pct": round(100.0 * (m3 - rec) / m3, 0) if m3 else None,
		})
	return {"weeks": weeks, "from": str(start), "rows": out}
