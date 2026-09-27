"""The daily water balance, wired to Frappe.

engine/balance.py holds the agronomy; this module gathers its inputs (settings,
block profiles, weather, irrometers, recorded irrigation), stores the result as
Irrigation Block Balance rows, and answers the dashboard's questions.

Recorded irrigation per block per day, strongest source first:
  1. the valve log — Valve Event intervals in which a valve of the block was open;
  2. the run sheet — Done cycles (their planned hours, or actual hours if entered)
     and Partial cycles (actual hours).
Section water meters are a cross-check in the budget, never an input here.
"""

import datetime

import frappe

from upande_irrigation.engine import balance as B

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
ESTIMATE_FROM_DAYS = 7
STALE_WEATHER_DAYS = 3
OPEN_STATES = ("Forced Open", "ON")


# ── settings ────────────────────────────────────────────────────


def agronomy():
	"""Irrigation Settings as engine parameters, defaults and the soil lookup."""
	cached = getattr(frappe.local, "_irr_agronomy", None)
	if cached:
		return cached
	s = frappe.get_cached_doc("Irrigation Settings")
	f = lambda v, d: float(v) if v not in (None, "") else d  # noqa: E731
	params = {
		"kpan": f(s.get("kpan"), 0.75) or 0.75,
		"depth_per_cup_mm": f(s.get("depth_per_cup_mm"), 0.5) or 0.5,
		"rain_loss_mm": f(s.get("rain_loss_mm"), 2.0),
		"rain_efficiency": f(s.get("rain_efficiency"), 0.9),
		"irrometer_weight": f(s.get("irrometer_weight"), 0.5),
		"bands": [
			{
				"age_from": float(b.age_from or 0),
				"age_to": float(b.age_to or 999),
				"root_depth_m": float(b.root_depth_m or 0.6),
				"canopy_pct": float(b.canopy_pct or 70),
				"kc": [float(b.get(f"kc_{m}") or 0.75) for m in MONTHS],
			}
			for b in sorted(s.get("age_bands") or [], key=lambda x: x.age_from or 0)
		],
		"tension_points": [(float(t.centibars), float(t.depletion_fraction)) for t in (s.get("tension_points") or [])],
	}
	if not params["bands"]:
		params["bands"] = [{"age_from": 0, "age_to": 999, "root_depth_m": 0.6, "canopy_pct": 70, "kc": [0.75] * 12}]
	defaults = {
		"default_planting_year": s.get("default_planting_year"),
		"default_soil_texture": s.get("default_soil_texture") or "Loam",
		"default_emitters_per_tree": f(s.get("default_emitters_per_tree"), 1.0),
		"default_emitter_flow_lph": f(s.get("default_emitter_flow_lph"), 0) or f(s.get("default_micro_output"), 70.0),
		"default_application_efficiency": f(s.get("default_application_efficiency"), 0.9),
		"default_depletion_fraction": f(s.get("default_depletion_fraction"), 0) or f(s.get("depletion_fraction"), 0.5),
		"default_plant_density": f(s.get("default_plant_density"), 250.0),
	}
	soils = {t.texture: float(t.awc_mm_per_m or 0) for t in (s.get("soil_textures") or [])}
	out = {"params": params, "defaults": defaults, "soils": soils, "settings": s}
	frappe.local._irr_agronomy = out
	return out


def resolved_profile(raw, year=None):
	a = agronomy()
	return B.resolve_profile(
		raw, a["defaults"], a["params"]["bands"], a["soils"], year or frappe.utils.getdate().year
	)


def profiles(farm=None):
	"""{block: {"resolved", "used", "farm", "section", "label"}} for active profiles."""
	filters = {"is_active": 1}
	if farm:
		filters["farm"] = farm
	out = {}
	for p in frappe.get_all("Irrigation Block Profile", filters=filters, fields=["*"]):
		r, used = resolved_profile(p)
		out[p.block] = {
			"resolved": r, "used": used, "farm": p.farm, "section": p.section,
			"label": frappe.db.get_value("Warehouse", p.block, "warehouse_name") or p.block,
		}
	return out


def balance_start():
	"""The balance's fixed origin. Set once (60 days back) so results don't drift daily."""
	s = frappe.get_single("Irrigation Settings")
	if not s.balance_start_date:
		s.db_set("balance_start_date", frappe.utils.add_days(frappe.utils.getdate(), -60))
		frappe.local._irr_agronomy = None
	return frappe.utils.getdate(s.balance_start_date)


# ── inputs ──────────────────────────────────────────────────────


def _days(start, end):
	d = start
	while d <= end:
		yield d
		d += datetime.timedelta(days=1)


def farm_weather(farm, start, end):
	"""{date: {rain, cups, estimated}} for every day, estimating any gap from the
	mean pan evaporation of the previous recorded days."""
	depth = agronomy()["params"]["depth_per_cup_mm"]
	rows = frappe.get_all(
		"Weather Reading",
		filters={"farm": farm, "date": ["between", [start - datetime.timedelta(days=30), end]]},
		fields=["date", "rainfall_mm", "pan_cups"],
		order_by="date asc",
		limit_page_length=0,
	)
	by_date = {frappe.utils.getdate(r.date): r for r in rows}
	recent = []
	out = {}
	for day in _days(start - datetime.timedelta(days=30), end):
		r = by_date.get(day)
		if r is not None:
			rain, cups = float(r.rainfall_mm or 0), float(r.pan_cups or 0)
			recent = (recent + [B.epan(rain, cups, depth)])[-ESTIMATE_FROM_DAYS:]
			rec = {"rain": rain, "cups": cups, "estimated": False}
		else:
			mean = sum(recent) / len(recent) if recent else 0.0
			rec = {"rain": 0.0, "cups": mean / depth if depth else 0.0, "estimated": True}
		if day >= start:
			out[day] = rec
	return out


def irrometers(blocks, start, end):
	"""{(block, date): 1 ft centibars}."""
	if not blocks:
		return {}
	rows = frappe.get_all(
		"Irrometer Reading",
		filters={"irrigation_block": ["in", list(blocks)], "date": ["between", [start, end]]},
		fields=["irrigation_block", "date", "irrometer_1ft_reading"],
		order_by="date asc",
		limit_page_length=0,
	)
	return {
		(r.irrigation_block, frappe.utils.getdate(r.date)): float(r.irrometer_1ft_reading)
		for r in rows
		if r.irrometer_1ft_reading is not None
	}


def _split_by_day(a, b):
	"""Hours of the interval [a, b) falling on each calendar day."""
	out = {}
	cur = a
	while cur < b:
		nxt = min(b, datetime.datetime.combine(cur.date() + datetime.timedelta(days=1), datetime.time.min))
		out[cur.date()] = out.get(cur.date(), 0.0) + (nxt - cur).total_seconds() / 3600.0
		cur = nxt
	return out


def valve_hours(blocks, start, end):
	"""{(block, date): hours} from Valve Event open intervals. A block's valves open
	together, so the block's hours are its longest-open valve's."""
	if not blocks:
		return {}
	lo = datetime.datetime.combine(start, datetime.time.min)
	hi = datetime.datetime.combine(end + datetime.timedelta(days=1), datetime.time.min)
	events = frappe.get_all(
		"Valve Event",
		filters={"block": ["in", list(blocks)], "at": ["<", hi]},
		fields=["valve", "block", "at", "to_state"],
		order_by="valve asc, at asc",
		limit_page_length=0,
	)
	per_valve = {}
	by_valve = {}
	for e in events:
		by_valve.setdefault(e.valve, []).append(e)
	now = frappe.utils.now_datetime()
	for valve, evs in by_valve.items():
		block = evs[0].block
		for i, e in enumerate(evs):
			if e.to_state not in OPEN_STATES:
				continue
			a = max(frappe.utils.get_datetime(e.at), lo)
			b = frappe.utils.get_datetime(evs[i + 1].at) if i + 1 < len(evs) else min(now, hi)
			b = min(b, hi)
			if b <= a:
				continue
			for day, h in _split_by_day(a, b).items():
				key = (valve, block, day)
				per_valve[key] = per_valve.get(key, 0.0) + h
	out = {}
	for (valve, block, day), h in per_valve.items():
		out[(block, day)] = max(out.get((block, day), 0.0), h)
	return out


def shift_members():
	"""{shift: [blocks]} from the Irrigation Scheduler's shift mapping."""
	out = {}
	for r in frappe.get_all(
		"Irrigation Shift Block",
		filters={"parenttype": "Irrigation Scheduler", "parentfield": "shift_blocks", "is_active": 1},
		fields=["shift", "block"],
		order_by="shift asc",
		limit_page_length=0,
	):
		out.setdefault(r.shift, []).append(r.block)
	return out


def runsheet_hours(farm, start, end, members=None):
	"""{(block, date): hours} from Done/Partial run-sheet cycles."""
	members = members or shift_members()
	rows = frappe.db.sql(
		"""
		SELECT r.shift, r.status, r.planned_hours, r.actual_hours, r.planned_start, r.actual_start, s.date
		FROM `tabIrrigation Run` r
		INNER JOIN `tabIrrigation Run Sheet` s ON s.name = r.parent
		WHERE r.parenttype = 'Irrigation Run Sheet' AND s.farm = %(farm)s
		  AND s.date BETWEEN %(start)s AND %(end)s AND r.status IN ('Done', 'Partial')
		""",
		{"farm": farm, "start": start, "end": end},
		as_dict=True,
	)
	out = {}
	for r in rows:
		hours = float(r.actual_hours or 0) if (r.status == "Partial" or r.actual_hours) else float(r.planned_hours or 0)
		when = r.actual_start or r.planned_start
		day = frappe.utils.getdate(when) if when else frappe.utils.getdate(r.date)
		for block in members.get(r.shift, []):
			out[(block, day)] = out.get((block, day), 0.0) + hours
	return out


# ── rebuild ─────────────────────────────────────────────────────

_ROW_FIELDS = (
	"name", "block", "farm", "date", "depletion_mm", "depletion_pct", "taw_mm", "raw_mm", "epan_mm",
	"etc_mm", "rain_mm", "effective_rain_mm", "irrigation_mm", "irrigation_source", "irrometer_cb",
	"irrometer_adjusted", "weather_estimated", "owner", "modified_by", "creation", "modified", "docstatus",
)


@frappe.whitelist()
def rebuild(farm=None, start=None, end=None):
	"""Recompute and store the balance for every active block (of `farm`), from the
	balance origin (or `start`) to yesterday (or `end`). Idempotent."""
	frappe.only_for(("System Manager", "Agriculture Manager", "Irrigation User"))
	origin = balance_start()
	start = frappe.utils.getdate(start) if start else origin
	end = frappe.utils.getdate(end) if end else frappe.utils.add_days(frappe.utils.getdate(), -1)
	if end < start:
		return {"blocks": 0, "rows": 0}
	a = agronomy()
	members = shift_members()
	by_farm = {}
	for block, p in profiles(farm).items():
		by_farm.setdefault(p["farm"], {})[block] = p

	total_rows = 0
	now = frappe.utils.now()
	for f, blocks in by_farm.items():
		if not f:
			continue
		# A partial rebuild starts from the stored depletion of the day before.
		prev = {}
		if start > origin:
			for r in frappe.get_all(
				"Irrigation Block Balance",
				filters={"farm": f, "date": frappe.utils.add_days(start, -1)},
				fields=["block", "depletion_mm"],
			):
				prev[r.block] = float(r.depletion_mm or 0)
		weather = farm_weather(f, start, end)
		irro = irrometers(blocks.keys(), start, end)
		valves = valve_hours(blocks.keys(), start, end)
		sheet = runsheet_hours(f, start, end, members)
		frappe.db.delete("Irrigation Block Balance", {"farm": f, "date": ["between", [start, end]]})
		values = []
		for block, p in blocks.items():
			days = []
			for day in _days(start, end):
				w = weather[day]
				hours, source = 0.0, ""
				if valves.get((block, day)):
					hours, source = valves[(block, day)], "Valve Log"
				elif sheet.get((block, day)):
					hours, source = sheet[(block, day)], "Run Sheet"
				days.append({
					"date": day, "month": day.month, "rain": w["rain"], "cups": w["cups"],
					"estimated": w["estimated"], "irrigation_hours": hours, "irrigation_source": source,
					"irrometer_cb": irro.get((block, day)),
				})
			for r in B.simulate(days, p["resolved"], a["params"], d0=prev.get(block, 0.0)):
				taw_mm = r["taw_mm"] or 0
				values.append((
					frappe.generate_hash(length=12), block, f, r["date"], r["depletion_mm"],
					round(100.0 * r["depletion_mm"] / taw_mm, 1) if taw_mm else 0, taw_mm, r["raw_mm"],
					r["epan_mm"], r["etc_mm"], r["rain_mm"], r["effective_rain_mm"], r["irrigation_mm"],
					# Float columns are NOT NULL in v16; irrometer_adjusted says whether 0 is a reading.
					r["irrigation_source"] if r["irrigation_mm"] else "", r["irrometer_cb"] or 0,
					int(r["irrometer_adjusted"]), int(r["weather_estimated"]),
					"Administrator", "Administrator", now, now, 0,
				))
		for i in range(0, len(values), 5000):
			frappe.db.bulk_insert("Irrigation Block Balance", _ROW_FIELDS, values[i : i + 5000])
		total_rows += len(values)
	frappe.db.commit()
	return {"blocks": sum(len(b) for b in by_farm.values()), "rows": total_rows, "from": str(start), "to": str(end)}


# ── questions ───────────────────────────────────────────────────


def state(farm=None, as_of=None):
	"""Per block: today's opening depletion, TAW/RAW, recent mean ETc and the projection."""
	as_of = frappe.utils.getdate(as_of) if as_of else frappe.utils.getdate()
	yesterday = frappe.utils.add_days(as_of, -1)
	profs = profiles(farm)
	if not profs:
		return {}
	rows = frappe.get_all(
		"Irrigation Block Balance",
		filters={"block": ["in", list(profs)], "date": ["between", [frappe.utils.add_days(as_of, -7), yesterday]]},
		fields=["block", "date", "depletion_mm", "etc_mm", "weather_estimated"],
		order_by="date asc",
		limit_page_length=0,
	)
	last_irr = dict(frappe.db.sql(
		"""SELECT block, MAX(date) FROM `tabIrrigation Block Balance`
		   WHERE irrigation_mm > 0 AND block IN %(b)s GROUP BY block""",
		{"b": tuple(profs)},
	))
	by_block = {}
	for r in rows:
		by_block.setdefault(r.block, []).append(r)
	out = {}
	for block, p in profs.items():
		r = p["resolved"]
		series = by_block.get(block, [])
		d0 = float(series[-1].depletion_mm) if series else 0.0
		etcs = [float(x.etc_mm or 0) for x in series]
		mean_etc = sum(etcs) / len(etcs) if etcs else 0.0
		proj = B.project(d0, mean_etc, r["raw_mm"], horizon=7)
		out[block] = {
			"block": block, "label": p["label"], "farm": p["farm"], "section": p["section"],
			"depletion_mm": round(d0, 2), "taw_mm": r["taw_mm"], "raw_mm": r["raw_mm"],
			"depletion_pct": round(100 * d0 / r["taw_mm"], 1) if r["taw_mm"] else 0,
			"mean_etc_mm": round(mean_etc, 2), "trigger_day": proj["trigger_day"],
			"projection": proj["depletion_by_day"], "rate_mm_hr": r["rate_mm_hr"],
			"application_efficiency": r["application_efficiency"], "block_flow_m3_hr": r["block_flow_m3_hr"],
			"area_ha": r["area_ha"], "using_defaults": p["used"],
			"weather_estimated": bool(series and series[-1].weather_estimated),
			"stale_weather": sum(1 for x in series[-STALE_WEATHER_DAYS:] if x.weather_estimated) >= STALE_WEATHER_DAYS,
			"last_irrigated": str(last_irr.get(block) or "") or None,
			"has_balance": bool(series),
		}
	return out


@frappe.whitelist()
def block_status(farm=None):
	return sorted(state(farm).values(), key=lambda x: (-(x["depletion_mm"] / x["raw_mm"]) if x["raw_mm"] else 0, x["label"]))


@frappe.whitelist()
def block_series(block, days=30):
	days = max(7, min(int(days or 30), 365))
	start = frappe.utils.add_days(frappe.utils.getdate(), -days)
	rows = frappe.get_all(
		"Irrigation Block Balance",
		filters={"block": block, "date": [">=", start]},
		fields=["date", "depletion_mm", "taw_mm", "raw_mm", "etc_mm", "effective_rain_mm", "irrigation_mm",
		        "irrigation_source", "irrometer_adjusted", "weather_estimated"],
		order_by="date asc",
		limit_page_length=0,
	)
	st = state().get(block) or {}
	return {"rows": rows, "status": st}
