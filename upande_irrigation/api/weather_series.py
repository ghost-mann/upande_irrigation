"""One measure, any farm and period, bucketed by day / week / month / year,
with the same calendar dates in other years beside it.

Backs the Weather view's "Weather & water" card, which replaced four fixed
charts (rainfall, temperature, pan evaporation, soil deficit). The card picks a
measure from a dropdown; everything here answers for whichever one it asks.

Two kinds of measure:
  * totals  -- rainfall, pan evaporation, water pumped, electricity: a bucket is
               the sum of its days.
  * levels  -- temperature, soil deficit: a bucket is the mean of its days.

A day with no reading is missing, never zero. Vale logs weather on roughly 40%
of days; summing its gaps as dry days would make it read drier than it is. So
every series carries its coverage -- the share of days in the period that had a
reading -- and a bucket with no readings comes back as None, a gap.

Comparison years sit on the current period's calendar: the year being compared
is read over the same dates in that year, and each of its days is moved forward
onto this year's date before bucketing, so "September" lines up with September.
"""

import calendar
import datetime

import frappe
from frappe.utils import add_days, date_diff, flt, getdate, nowdate

TOTALS = ("rainfall", "evaporation", "water", "electricity")
LEVELS = ("temperature", "deficit_blocks", "deficit_swd")
MEASURES = TOTALS + LEVELS

UNITS = {
	"rainfall": "mm", "evaporation": "mm", "temperature": "°C",
	"deficit_blocks": "mm", "deficit_swd": "mm", "water": "m³", "electricity": "kWh",
}
LABELS = {
	"rainfall": "Rainfall", "evaporation": "Pan evaporation", "temperature": "Temperature",
	"deficit_blocks": "Soil deficit (block balance)", "deficit_swd": "Soil deficit (old SWD)",
	"water": "Water pumped", "electricity": "Electricity",
}
# a guard on the request, not a site choice: about ten years of days
MAX_DAYS = 3700

# Site choices live in Irrigation Settings ("Weather & water card"); these are
# only the values used where a setting is blank. A Single read before its new
# fields were ever saved gives 0 for them, so 0 also means "use the default".
DEFAULTS = {
	"weather_max_plausible_temp": 50.0,      # a maximum at or above this is a typing slip
	"weather_max_plausible_min_temp": 35.0,  # the same for minimums
	"long_term_min_years": 3,                # earlier years needed for a long-term average
	"long_term_min_coverage_pct": 60.0,      # share of a year's days that must be read to count
}


def card_settings():
	try:
		doc = frappe.get_cached_doc("Irrigation Settings")
	except Exception:
		doc = None
	out = {}
	for key, default in DEFAULTS.items():
		v = getattr(doc, key, None) if doc else None
		out[key] = type(default)(v) if v not in (None, "", 0) else default
	return out


# ── calendar helpers ─────────────────────────────────────────────────────────
def shift_year(d, years):
	"""The same calendar day `years` later (29 Feb lands on 28 Feb)."""
	y = d.year + years
	day = min(d.day, calendar.monthrange(y, d.month)[1])
	return d.replace(year=y, day=day)


def bucket_key(d, granularity):
	if granularity == "week":
		return d - datetime.timedelta(days=d.weekday())
	if granularity == "month":
		return d.replace(day=1)
	if granularity == "year":
		return d.replace(month=1, day=1)
	return d


def bucket_label(k, granularity):
	if granularity == "week":
		return "w/c " + k.strftime("%-d %b")
	if granularity == "month":
		return k.strftime("%b %Y")
	if granularity == "year":
		return k.strftime("%Y")
	return k.strftime("%-d %b")


def bucket_keys(start, end, granularity):
	keys, seen = [], set()
	d = start
	while d <= end:
		k = bucket_key(d, granularity)
		if k not in seen:
			seen.add(k)
			keys.append(k)
		d += datetime.timedelta(days=1)
	return keys


# ── daily readers: {date: value} (temperature: {date: (min, mean, max)}) ─────
def _farms_filter(farm):
	return {"farm": farm} if farm else {}


def _weather_rows(farm, start, end, fields):
	return frappe.get_all(
		"Weather Reading",
		filters={"date": ["between", [start, end]], **_farms_filter(farm)},
		fields=["date", "farm"] + fields,
		limit_page_length=0,
	)


def _avg_by_day(pairs):
	"""Average per day across farms (a farm-wide rainfall is the mean of the
	gauges that reported, not their sum)."""
	acc = {}
	for d, v in pairs:
		if v is None:
			continue
		s = acc.setdefault(d, [0.0, 0])
		s[0] += v
		s[1] += 1
	return {d: s[0] / s[1] for d, s in acc.items()}


def daily_values(measure, farm, start, end):
	"""Return ({date: value}, notes) for one measure over [start, end]."""
	notes = []
	if measure == "rainfall":
		rows = _weather_rows(farm, start, end, ["rainfall_mm"])
		return _avg_by_day((getdate(r.date), flt(r.rainfall_mm)) for r in rows), notes

	if measure == "evaporation":
		# the stored ET-pan where there is one, otherwise rain plus pan depth --
		# the rule the weather view has always used (api.weather.fetch)
		rows = _weather_rows(farm, start, end, ["et_pan", "rainfall_mm", "pan_depth_mm"])
		return _avg_by_day(
			(getdate(r.date), flt(r.et_pan) if flt(r.et_pan) > 0 else max(0.0, flt(r.rainfall_mm) + flt(r.pan_depth_mm)))
			for r in rows
		), notes

	if measure == "temperature":
		# 0 °C on these farms means "not recorded", as everywhere else in the app
		rows = _weather_rows(farm, start, end, ["minimum_temperature", "maximum_temperature", "temperature_average"])
		cfg = card_settings()
		acc, dropped = {}, []
		for r in rows:
			lo = flt(r.minimum_temperature) if flt(r.minimum_temperature) > 0 else None
			hi = flt(r.maximum_temperature) if flt(r.maximum_temperature) > 0 else None
			# a typed reading beyond what the site's climate allows (Irrigation
			# Settings) is a typo, not weather -- one 2689 for 26.89 lifted a
			# period's mean by 16 °C. Dropped from the figures, and named so
			# someone can correct it.
			if lo is not None and not lo < cfg["weather_max_plausible_min_temp"]:
				dropped.append(f"{r.date:%-d %b %Y} min {lo:g}")
				lo = None
			if hi is not None and not hi < cfg["weather_max_plausible_temp"]:
				dropped.append(f"{r.date:%-d %b %Y} max {hi:g}")
				hi = None
			# the mean is (min + max) / 2 wherever both were read; a stored average
			# is used only when it is not and it is believable -- some rows carry
			# a stored "average" well above the day's maximum
			if lo is not None and hi is not None:
				mean = (lo + hi) / 2
			else:
				stored = flt(r.temperature_average)
				mean = stored if 0 < stored < cfg["weather_max_plausible_temp"] else None
			if mean is None and lo is None and hi is None:
				continue
			acc.setdefault(getdate(r.date), []).append((lo, mean, hi))
		out = {}
		for d, vals in acc.items():
			def avg(i):
				xs = [v[i] for v in vals if v[i] is not None]
				return sum(xs) / len(xs) if xs else None
			out[d] = (avg(0), avg(1), avg(2))
		if dropped:
			notes.append(f"Ignored {len(dropped)} impossible temperature reading{'s' if len(dropped) != 1 else ''} "
			             f"({'; '.join(dropped[:5])}{' …' if len(dropped) > 5 else ''}). Correct the Weather Reading to include it.")
		return out, notes

	if measure == "deficit_swd":
		rows = _weather_rows(farm, start, end, ["swd"])
		return _avg_by_day((getdate(r.date), r.swd if r.swd is not None else None) for r in rows), notes

	if measure == "deficit_blocks":
		rows = frappe.get_all(
			"Irrigation Block Balance",
			filters={"date": ["between", [start, end]], **_farms_filter(farm)},
			fields=["date", "block", "depletion_mm"],
			limit_page_length=0,
		)
		acc = {}
		for r in rows:
			if r.depletion_mm is None:
				continue
			s = acc.setdefault(getdate(r.date), [0.0, 0, None, None])
			s[0] += flt(r.depletion_mm)
			s[1] += 1
			if s[2] is None or flt(r.depletion_mm) > s[2]:
				s[2], s[3] = flt(r.depletion_mm), r.block
		# the farm average, and the driest block beside it
		return {d: (s[0] / s[1], s[2], s[3]) for d, s in acc.items()}, notes

	if measure in ("water", "electricity"):
		doctype = "Water Meter Reading" if measure == "water" else "Electricity Meter Reading"
		sec_filters = {"warehouse_type": "Section"}
		if farm:
			sec_filters["custom_farm"] = farm
		sections = frappe.get_all("Warehouse", filters=sec_filters, pluck="name", limit_page_length=0)
		if not sections:
			return {}, notes
		rows = frappe.get_all(
			doctype,
			filters={"date": ["between", [start, f"{end} 23:59:59"]], "docstatus": ["!=", 2],
			         "irrigation_section": ["in", sections]},
			fields=["date", "previous_reading", "new_reading", "units_used"],
			limit_page_length=0,
		)
		out, tenfold = {}, 0
		for r in rows:
			used = flt(r.units_used)
			diff = flt(r.new_reading) - flt(r.previous_reading)
			# some imported rows hold 10 x (new - previous) rather than the difference
			if diff > 0 and abs(used - 10 * diff) < 0.01 and abs(used - diff) > 0.01:
				tenfold += 1
			d = getdate(r.date)
			out[d] = out.get(d, 0.0) + used
		if tenfold:
			notes.append(f"{tenfold} reading{'s' if tenfold != 1 else ''} in this period store usage as 10 × (new − previous) "
			             "rather than the difference; shown as recorded. Check the meter's units before relying on them.")
		return out, notes

	frappe.throw(f"Unknown measure {measure!r}")


# ── aggregation ──────────────────────────────────────────────────────────────
def aggregate(values, keys, granularity, measure, day_offset_years=0):
	"""Bucket a {date: value} map onto `keys`. A comparison year's dates are
	first moved forward by `day_offset_years` onto the current calendar."""
	by_key = {k: [] for k in keys}
	for d, v in values.items():
		dd = shift_year(d, day_offset_years) if day_offset_years else d
		k = bucket_key(dd, granularity)
		if k in by_key:
			by_key[k].append(v)
	out = []
	for k in keys:
		vs = by_key[k]
		if not vs:
			out.append(None)
		elif measure == "temperature":
			def avg(i):
				xs = [v[i] for v in vs if v[i] is not None]
				return round(sum(xs) / len(xs), 2) if xs else None
			out.append({"min": avg(0), "mean": avg(1), "max": avg(2)})
		elif measure == "deficit_blocks":
			out.append({"mean": round(sum(v[0] for v in vs) / len(vs), 2),
			            "driest": round(max(v[1] for v in vs), 2),
			            "driest_block": max(vs, key=lambda v: v[1])[2]})
		elif measure in TOTALS:
			out.append(round(sum(vs), 2))
		else:
			out.append(round(sum(vs) / len(vs), 2))
	return out


def coverage(values, start, end):
	"""Share of the window's days that had a reading, never above 1."""
	span = date_diff(end, start) + 1
	return round(min(1.0, len(values) / span), 3) if span > 0 else 0


def headline(values, measure):
	"""One figure for the whole period: a total, or a mean."""
	if not values:
		return None
	if measure in TOTALS:
		return round(sum(values.values()), 2)
	if measure == "temperature":
		xs = [v[1] for v in values.values() if v[1] is not None]
	elif measure == "deficit_blocks":
		xs = [v[0] for v in values.values()]
	else:
		xs = [v for v in values.values() if v is not None]
	return round(sum(xs) / len(xs), 2) if xs else None


def first_reading(measure, farm):
	"""The earliest day this measure was recorded for the farm (all farms if none)."""
	if measure in ("water", "electricity"):
		doctype = "Water Meter Reading" if measure == "water" else "Electricity Meter Reading"
		sec_filters = {"warehouse_type": "Section"}
		if farm:
			sec_filters["custom_farm"] = farm
		sections = frappe.get_all("Warehouse", filters=sec_filters, pluck="name", limit_page_length=0) or ["__none__"]
		v = frappe.db.sql(f"SELECT MIN(DATE(`date`)) FROM `tab{doctype}` WHERE irrigation_section IN %s AND docstatus != 2", (tuple(sections),))
	elif measure == "deficit_blocks":
		cond, args = ("WHERE farm = %s", (farm,)) if farm else ("", ())
		v = frappe.db.sql(f"SELECT MIN(`date`) FROM `tabIrrigation Block Balance` {cond}", args)
	else:
		cond, args = ("WHERE farm = %s", (farm,)) if farm else ("", ())
		v = frappe.db.sql(f"SELECT MIN(`date`) FROM `tabWeather Reading` {cond}", args)
	return getdate(v[0][0]) if v and v[0][0] else None


def years_with_data(measure, farm):
	"""Years that hold any reading for this measure, newest first."""
	if measure in ("water", "electricity"):
		doctype = "Water Meter Reading" if measure == "water" else "Electricity Meter Reading"
		sec_filters = {"warehouse_type": "Section"}
		if farm:
			sec_filters["custom_farm"] = farm
		sections = frappe.get_all("Warehouse", filters=sec_filters, pluck="name", limit_page_length=0) or ["__none__"]
		rows = frappe.db.sql(f"SELECT DISTINCT YEAR(`date`) FROM `tab{doctype}` WHERE irrigation_section IN %s AND docstatus != 2",
		                     (tuple(sections),))
	elif measure == "deficit_blocks":
		cond, args = ("WHERE farm = %s", (farm,)) if farm else ("", ())
		rows = frappe.db.sql(f"SELECT DISTINCT YEAR(`date`) FROM `tabIrrigation Block Balance` {cond}", args)
	else:
		col = {"rainfall": "rainfall_mm", "evaporation": "pan_depth_mm", "temperature": "maximum_temperature",
		       "deficit_swd": "swd"}[measure]
		cond = f"WHERE `{col}` IS NOT NULL" + (" AND farm = %s" if farm else "")
		if measure == "deficit_swd":
			cond += " AND swd != 0"
		rows = frappe.db.sql(f"SELECT DISTINCT YEAR(`date`) FROM `tabWeather Reading` {cond}", (farm,) if farm else ())
	return sorted((int(r[0]) for r in rows if r[0]), reverse=True)


@frappe.whitelist()
def series(measure="rainfall", farm="", from_date="", to_date="", granularity="month",
           compare_years="", long_term=1):
	measure = measure if measure in MEASURES else "rainfall"
	granularity = granularity if granularity in ("day", "week", "month", "year") else "month"
	end = getdate(to_date or nowdate())
	start = getdate(from_date or end.replace(month=1, day=1))
	if start > end:
		start, end = end, start
	if date_diff(end, start) > MAX_DAYS:
		start = add_days(end, -MAX_DAYS)
	days = date_diff(end, start) + 1

	keys = bucket_keys(start, end, granularity)
	labels = [bucket_label(k, granularity) for k in keys]

	current, notes = daily_values(measure, farm, start, end)
	out_series = [{
		"key": "current", "label": f"{start.year}" if start.year == end.year else f"{start.year}–{str(end.year)[-2:]}",
		"values": aggregate(current, keys, granularity, measure),
		"headline": headline(current, measure),
		"coverage": coverage(current, start, end),
	}]

	available = years_with_data(measure, farm)
	wanted = []
	for y in str(compare_years or "").split(","):
		y = y.strip()
		if y.isdigit():
			wanted.append(int(y))
	for y in wanted:
		offset = start.year - y
		if offset <= 0:
			continue
		ys, ye = shift_year(start, -offset), shift_year(end, -offset)
		vals, n = daily_values(measure, farm, ys, ye)
		notes += [x for x in n if x not in notes]
		# like for like: the same year over only the days this period has readings
		# for, so a week not yet logged here does not read as a dry week there
		cur_days = set(current)
		matched = {dd: v for dd, v in vals.items() if shift_year(dd, offset) in cur_days}
		out_series.append({
			"headline_same_days": headline(matched, measure),
			"key": str(y),
			"label": str(y) if start.year == end.year else f"{start.year - offset}–{str(end.year - offset)[-2:]}",
			"values": aggregate(vals, keys, granularity, measure, day_offset_years=offset),
			"headline": headline(vals, measure),
			"coverage": coverage(vals, ys, ye),
		})

	# the long-term average: every earlier year that recorded enough of the period
	cfg = card_settings()
	if frappe.utils.cint(long_term) and measure != "deficit_blocks":
		per_year = []
		for y in available:
			offset = start.year - y
			if offset <= 0:
				continue
			ys, ye = shift_year(start, -offset), shift_year(end, -offset)
			vals, _ = daily_values(measure, farm, ys, ye)
			if coverage(vals, ys, ye) >= cfg["long_term_min_coverage_pct"] / 100:
				matched = {dd: v for dd, v in vals.items() if shift_year(dd, offset) in set(current)}
				per_year.append((y, aggregate(vals, keys, granularity, measure, day_offset_years=offset),
				                 headline(vals, measure), headline(matched, measure)))
		if len(per_year) >= cfg["long_term_min_years"]:
			def mean_of(xs):
				xs = [x for x in xs if x is not None]
				return round(sum(xs) / len(xs), 2) if xs else None
			if measure == "temperature":
				avg_vals = [
					{"min": mean_of([(p[1][i] or {}).get("min") for p in per_year]),
					 "mean": mean_of([(p[1][i] or {}).get("mean") for p in per_year]),
					 "max": mean_of([(p[1][i] or {}).get("max") for p in per_year])}
					if any(p[1][i] for p in per_year) else None
					for i in range(len(keys))
				]
			else:
				avg_vals = [mean_of([p[1][i] for p in per_year]) for i in range(len(keys))]
			out_series.append({
				"key": "avg",
				"label": f"Average {min(p[0] for p in per_year)}–{max(p[0] for p in per_year)} ({len(per_year)} yrs)",
				"values": avg_vals,
				"headline": mean_of([p[2] for p in per_year]),
				"headline_same_days": mean_of([p[3] for p in per_year]),
				"coverage": None,
				"years": sorted(p[0] for p in per_year),
			})

	if measure == "deficit_swd":
		notes.append("Old model: the single farm-level SWD stored on each weather reading, kept for history. "
		             "Irrigation decisions now use the per-block balance.")
	data_from = first_reading(measure, farm)
	if measure == "deficit_blocks":
		notes.append("Per-block daily water balance: the farm average, with the driest block beside it."
		             + (f" Recorded from {data_from:%-d %b %Y}, so earlier years have nothing to compare." if data_from else ""))

	return {
		"measure": measure, "label": LABELS[measure], "unit": UNITS[measure],
		"kind": "total" if measure in TOTALS else "level",
		"farm": farm or None, "from_date": str(start), "to_date": str(end),
		"granularity": granularity, "days": days,
		"buckets": [str(k) for k in keys], "labels": labels,
		"series": out_series, "years_available": available, "notes": notes,
		"data_from": str(data_from) if data_from else None,
	}
