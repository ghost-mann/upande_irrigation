"""Daily soil water balance for one block — FAO-56, adapted for micro-irrigated avocado.

Pure: plain numbers and dicts in, plain dicts out; no database, no clock.
api/balance.py gathers the inputs and stores the result.

    Epan      = rain + pan_cups × depth_per_cup        refill-method Class A pan
    ETc       = Epan × Kp × Kc(age, month) × Kr        crop water use
    Kr        = GC + 0.5 × (1 − GC)                    micro-irrigation, partial cover
    TAW       = available water (mm/m) × root depth    what the root zone holds
    RAW       = p × TAW                                irrigate once depletion reaches it
    eff_rain  = min((rain − loss) × efficiency, room)  surplus drains, it is not banked
    irr_net   = hours × rate × application efficiency
    D         = clamp(D_prev + ETc − eff_rain − irr_net, 0, TAW)

D is the root-zone depletion below field capacity, in mm: 0 is a full profile, TAW is the
wilting point. The weekly planner this replaces had no D at all, subtracted rain that the
pan figure already contained, and divided hours by coverage instead of reducing crop use.
"""


def epan(rain, cups, depth_per_cup):
	"""Pan evaporation from the refill method: water added (or, after rain, removed —
	negative cups) plus the rain that fell into the pan."""
	return max(0.0, float(rain or 0) + float(cups or 0) * float(depth_per_cup or 0))


def band_for(age, bands):
	"""The age band (a dict with age_from/age_to) containing `age` years."""
	a = float(age or 0)
	for b in bands or []:
		if float(b["age_from"]) <= a <= float(b["age_to"]):
			return b
	return (bands or [None])[-1]


def kc_for(age, month, bands):
	b = band_for(age, bands)
	if not b:
		return 0.75
	return float(b["kc"][int(month) - 1])


def kr(canopy_pct):
	"""Reduction for partial ground cover under micro-irrigation (Keller & Karmeli)."""
	gc = min(1.0, max(0.0, float(canopy_pct or 0) / 100.0))
	return gc + 0.5 * (1.0 - gc)


def taw(awc_mm_per_m, root_m):
	return max(0.0, float(awc_mm_per_m or 0) * float(root_m or 0))


def rate_mm_hr(trees, emitters_per_tree, lph, area_ha):
	"""Application rate over the whole block. Zero, never an error, when a figure is missing."""
	area_m2 = float(area_ha or 0) * 10000.0
	litres = float(trees or 0) * float(emitters_per_tree or 0) * float(lph or 0)
	if area_m2 <= 0 or litres <= 0:
		return 0.0
	return litres / area_m2


def effective_rain(rain, loss_mm, efficiency, room):
	"""Rain that stays in the root zone: small events are lost to interception and
	evaporation, and anything beyond the room left drains away."""
	eff = max(0.0, float(rain or 0) - float(loss_mm or 0)) * float(efficiency or 0)
	return max(0.0, min(eff, float(room or 0)))


def step(prev_d, etc, rain_eff, irr_net, taw_mm):
	d = float(prev_d) + float(etc) - float(rain_eff) - float(irr_net)
	return min(max(d, 0.0), float(taw_mm))


def tension_fraction(cb, points):
	"""Depletion fraction of TAW implied by a soil-tension reading, by linear
	interpolation over (centibars, fraction) points."""
	pts = sorted(points or [])
	c = float(cb)
	if not pts:
		return 0.0
	if c <= pts[0][0]:
		return float(pts[0][1])
	for (c0, f0), (c1, f1) in zip(pts, pts[1:]):
		if c <= c1:
			return float(f0) + (float(f1) - float(f0)) * (c - c0) / (c1 - c0)
	return float(pts[-1][1])


def blend(d, cb, taw_mm, points, weight):
	"""Move the modelled depletion toward what the irrometer implies."""
	implied = tension_fraction(cb, points) * float(taw_mm)
	return float(d) + float(weight) * (implied - float(d))


def hours_to_refill(net_mm, rate, application_efficiency):
	if float(rate or 0) <= 0 or float(application_efficiency or 0) <= 0:
		return 0.0
	return float(net_mm) / float(application_efficiency) / float(rate)


def simulate(days, profile, params, d0=0.0):
	"""Run the balance over consecutive days. Each day is a dict with date, month, rain,
	cups, irrigation_hours, irrometer_cb and estimated; returns one dict per day.

	`profile` is already resolved (defaults applied): age_years, awc_mm_per_m,
	root_depth_m, canopy_pct, rate_mm_hr, application_efficiency, depletion_fraction.
	"""
	taw_mm = taw(profile["awc_mm_per_m"], profile["root_depth_m"])
	raw_mm = float(profile["depletion_fraction"]) * taw_mm
	k_r = kr(profile["canopy_pct"])
	d = min(max(float(d0), 0.0), taw_mm)
	out = []
	for day in days:
		e = epan(day.get("rain"), day.get("cups"), params["depth_per_cup_mm"])
		etc = e * float(params["kpan"]) * kc_for(profile["age_years"], day["month"], params["bands"]) * k_r
		rain = float(day.get("rain") or 0)
		eff = effective_rain(rain, params["rain_loss_mm"], params["rain_efficiency"], room=d + etc)
		irr = float(day.get("irrigation_hours") or 0) * float(profile["rate_mm_hr"]) * float(profile["application_efficiency"])
		d = step(d, etc, eff, irr, taw_mm)
		adjusted = False
		if day.get("irrometer_cb") is not None:
			d = min(max(blend(d, day["irrometer_cb"], taw_mm, params["tension_points"], params["irrometer_weight"]), 0.0), taw_mm)
			adjusted = True
		out.append({
			"date": day["date"],
			"epan_mm": round(e, 3),
			"etc_mm": round(etc, 3),
			"rain_mm": round(rain, 3),
			"effective_rain_mm": round(eff, 3),
			"irrigation_mm": round(irr, 3),
			"irrigation_source": day.get("irrigation_source") or ("Run Sheet" if irr else ""),
			"depletion_mm": round(d, 3),
			"taw_mm": round(taw_mm, 3),
			"raw_mm": round(raw_mm, 3),
			"irrometer_cb": day.get("irrometer_cb"),
			"irrometer_adjusted": adjusted,
			"weather_estimated": bool(day.get("estimated")),
		})
	return out


def project(d0, mean_etc, raw_mm, horizon=7):
	"""Step forward with no rain. trigger_day is the first day (0 = today, already past)
	on which depletion reaches RAW, or None within the horizon."""
	trigger = 0 if float(d0) >= float(raw_mm) else None
	series = []
	d = float(d0)
	for k in range(1, int(horizon) + 1):
		d += float(mean_etc)
		series.append(round(d, 3))
		if trigger is None and d >= float(raw_mm):
			trigger = k
	return {"trigger_day": trigger, "depletion_by_day": series}
