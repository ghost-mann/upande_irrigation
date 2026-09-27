"""A day's run sheet from block water states. Pure: no database, no clock.

    shift_need  a shift's blocks open together, so the DRIEST member sets the run:
                net = its depletion at the end of today (refill to field capacity),
                hours = net ÷ application efficiency ÷ application rate.
                Due    when that member reaches RAW by the end of today;
                Ahead  when it will within `irrigate_ahead_days` — only placed in
                       time the Due shifts leave free.
    place       per pump, one shift at a time, most urgent first, inside the pump's
                run windows; a shift's cycles are separated by cycle_rest_hours and
                another shift may use the pump meanwhile. Whatever does not fit is
                returned as "Not placed" — never silently dropped.
"""

import datetime

from upande_irrigation.engine.allocate import cycle_plan
from upande_irrigation.engine.balance import hours_to_refill

_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def shift_need(members, settings):
	reasons = []
	ahead = int(settings.get("irrigate_ahead_days") or 0)
	worst = None
	for m in members:
		raw = float(m.get("raw_mm") or 0)
		if raw <= 0:
			continue
		end_today = float(m.get("depletion_mm") or 0) + float(m.get("mean_etc_mm") or 0)
		taw_mm = float(m.get("taw_mm") or 0)
		if taw_mm:
			end_today = min(end_today, taw_mm)  # the root zone cannot be drier than empty
		score = end_today / raw
		if worst is None or score > worst[0]:
			worst = (score, end_today, m)
	if worst is None:
		return {"kind": None, "hours": 0.0, "net_mm": 0.0, "urgency": 0.0, "trigger_day": None, "reasons": ["no block with a water balance"]}

	score, net, m = worst
	trigger = m.get("trigger_day")
	if score >= 1 or trigger in (0, 1):
		kind = "Due"
	elif trigger is not None and trigger <= 1 + ahead:
		kind = "Ahead"
	else:
		kind = None

	rate = float(m.get("rate_mm_hr") or 0)
	hours = hours_to_refill(net, rate, m.get("application_efficiency") or 0.9)
	if rate <= 0:
		reasons.append(f"{m.get('label') or 'a block'} has no application rate (area, trees or emitters missing) — set its Irrigation Block Profile")
		hours = 0.0
	lo = float(settings.get("min_run_hours") or 0)
	hi = float(settings.get("max_run_hours_per_day") or 0)
	if hours and hours < lo:
		hours = lo
	if hi and hours > hi:
		reasons.append(f"capped at {hi:g} h for the day; the rest carries to tomorrow")
		hours = hi
	return {"kind": kind, "hours": round(hours, 2), "net_mm": round(net, 2), "urgency": round(score, 3),
	        "trigger_day": trigger, "reasons": reasons}


def windows_for_date(date, rows, rest_dates=""):
	"""[(start, end)] datetimes for a pump on `date`. An end before its start runs
	past midnight. A rest date means no windows at all."""
	rest = {x.strip() for x in (rest_dates or "").splitlines() if x.strip()}
	if str(date) in rest:
		return []
	day = _DAYS[date.weekday()]
	out = []
	for r in rows or []:
		if r.get("day") not in ("Every day", day):
			continue
		s = _time(r.get("start_time"))
		e = _time(r.get("end_time"))
		if s is None or e is None:
			continue
		start = datetime.datetime.combine(date, s)
		end = datetime.datetime.combine(date, e)
		if end <= start:
			end += datetime.timedelta(days=1)
		out.append((start, end))
	return merge(out)


def merge(intervals):
	"""Sorted, with overlapping or touching intervals joined — so two windows that
	overlap can never let the pump be booked twice for the same hour."""
	out = []
	for a, b in sorted(intervals):
		if out and a <= out[-1][1]:
			out[-1] = (out[-1][0], max(out[-1][1], b))
		else:
			out.append((a, b))
	return out


def free(windows, busy=(), not_before=None):
	"""Windows minus the busy intervals and minus everything before `not_before`."""
	cut = merge(list(busy or []) + ([(datetime.datetime.min, not_before)] if not_before else []))
	out = []
	for a, b in merge(windows):
		segs = [(a, b)]
		for c, d in cut:
			nxt = []
			for x, y in segs:
				if d <= x or c >= y:
					nxt.append((x, y))
					continue
				if x < c:
					nxt.append((x, c))
				if d < y:
					nxt.append((d, y))
			segs = nxt
		out.extend(s for s in segs if s[1] > s[0])
	return out


def _time(v):
	if v is None or v == "":
		return None
	if isinstance(v, datetime.time):
		return v
	if isinstance(v, datetime.timedelta):
		return (datetime.datetime.min + v).time()
	parts = [int(float(x)) for x in str(v).split(":")[:3]]
	while len(parts) < 3:
		parts.append(0)
	return datetime.time(*parts)


def place(requests_by_pump, windows_by_pump, settings, busy=None, not_before=None):
	"""`busy`: {pump: [(start, end)]} already taken (cycles kept on the sheet);
	`not_before`: no cycle starts earlier (now, when planning today)."""
	rest = datetime.timedelta(hours=float(settings.get("cycle_rest_hours") or 0))
	out = []
	for pump, requests in requests_by_pump.items():
		all_windows = list(windows_by_pump.get(pump) or [])
		windows = free(all_windows, (busy or {}).get(pump), not_before)
		no_rate = [r for r in requests if not r["hours"]]
		for r in no_rate:
			out.append(_row(pump, r, 1, 1, None, None, 0, "Not placed"))
		requests = [r for r in requests if r["hours"]]
		pending = []
		order = sorted(requests, key=lambda r: (r["kind"] != "Due", -float(r["urgency"] or 0)))
		for rank, r in enumerate(order):
			count, each = cycle_plan(float(r["hours"] or 0), settings)
			for i in range(count):
				pending.append({"req": r, "rank": rank, "cycle": i + 1, "cycles": count, "each": each,
				                "ready": None})
		if not windows:
			why = "no run window for this pump today" if not all_windows else "no free window time left today"
			for r in order:
				out.append(_row(pump, r, 1, 1, None, None, r["hours"], "Not placed", extra=why))
			continue

		cursor = windows[0][0]
		wi = 0
		placed_end = {}  # shift → end of its last placed cycle
		while pending and wi < len(windows):
			w_start, w_end = windows[wi]
			cursor = max(cursor, w_start)
			ready = [p for p in pending if _ready(p, placed_end, rest) <= cursor
			         and (p["cycle"] == 1 or p["req"]["shift"] in placed_end)]
			fits = [p for p in ready if cursor + datetime.timedelta(hours=p["each"]) <= w_end]
			if fits:
				p = min(fits, key=lambda x: (x["rank"], x["cycle"]))
				end = cursor + datetime.timedelta(hours=p["each"])
				out.append(_row(pump, p["req"], p["cycle"], p["cycles"], cursor, end, p["each"], "Planned"))
				placed_end[p["req"]["shift"]] = end
				pending.remove(p)
				cursor = end
				continue
			# Nothing fits now: wait for a resting cycle, or move to the next window.
			waits = [_ready(p, placed_end, rest) for p in pending
			         if p["cycle"] > 1 and p["req"]["shift"] in placed_end and _ready(p, placed_end, rest) > cursor]
			nxt = min(waits) if waits else None
			if nxt and nxt < w_end:
				cursor = nxt
			else:
				wi += 1
				if wi < len(windows):
					cursor = max(cursor, windows[wi][0])

		left = {}
		for p in pending:
			key = p["req"]["shift"]
			left.setdefault(key, {"req": p["req"], "hours": 0.0})
			left[key]["hours"] += p["each"]
		for v in left.values():
			out.append(_row(pump, v["req"], 1, 1, None, None, round(v["hours"], 2), "Not placed",
			                extra="the pump's run windows are full today"))
	return out


def _ready(p, placed_end, rest):
	prev = placed_end.get(p["req"]["shift"])
	return prev + rest if (prev and p["cycle"] > 1) else datetime.datetime.min


def _row(pump, req, cycle, cycles, start, end, hours, status, extra=None):
	reasons = list(req.get("reasons") or [])
	if extra:
		reasons.append(extra)
	return {
		"shift": req["shift"], "section": req.get("section"), "pump": pump, "kind": req["kind"],
		"cycle_no": cycle, "cycles": cycles, "planned_start": start, "planned_end": end,
		"planned_hours": round(float(hours), 2), "net_mm": req.get("net_mm"), "urgency": req.get("urgency"),
		"status": status, "reason": "; ".join(reasons),
	}
