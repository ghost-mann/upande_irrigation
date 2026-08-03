"""What one shift needs, from a week that has already happened.

Pure: takes plain dicts, touches no database and no clock. That is what makes the
old defect impossible to reintroduce — the caller has to hand this module a
window, and the only window measured_window() will hand back is an elapsed one.

The agronomic formulas are unchanged from the original Server Script:
    et_pan     = rainfall + (pan_cups × depth_per_cup_mm)     [Weather Reading]
    et_crop    = et_pan × pan_to_crop_coefficient             [Weather Reading]
    clean_et   = Σ et_crop × et_crop_coefficient              [here]
    deficit    = max(0, clean_et − Σ rainfall)                [here]
    hours      = deficit ÷ mm_hr ÷ (coverage ÷ 100)           [here]
"""

import datetime

MEASURE_DAYS = 7


def measured_window(plan_from, days=MEASURE_DAYS):
	"""The elapsed week whose losses the plan replaces.

	Ends the day before the planned week opens, so at generation time every day
	in it is settled. The old engine aggregated the planned week itself — six
	days in the future at generation time — which is why 819 of 836 planners
	found no weather and reported no demand.
	"""
	end = plan_from - datetime.timedelta(days=1)
	start = end - datetime.timedelta(days=days - 1)
	return start, end


def aggregate(readings):
	"""Sum a list of Weather Reading rows into the figures demand needs."""
	days = 0
	rainfall = et_pan = et_crop = 0.0
	temp_total = 0.0
	temp_days = 0

	for r in readings or []:
		days += 1
		rainfall += float(r.get("rainfall_mm") or 0)
		et_pan += float(r.get("et_pan") or 0)
		et_crop += float(r.get("et_crop") or 0)
		tmin = float(r.get("minimum_temperature") or 0)
		tmax = float(r.get("maximum_temperature") or 0)
		# A 0 °C reading means "not recorded", which is how the planner has always
		# treated it. Averaging it in would drag the mean down and suppress the
		# anthracnose index.
		if tmin > 0 and tmax > 0:
			temp_total += (tmin + tmax) / 2.0
			temp_days += 1

	return {
		"days": days,
		"rainfall_mm": round(rainfall, 4),
		"et_pan_mm": round(et_pan, 4),
		"et_crop_mm": round(et_crop, 4),
		"mean_temp": round(temp_total / temp_days, 4) if temp_days else 0.0,
		"temp_days": temp_days,
	}


def is_plannable(agg, settings):
	"""Whether the measured week is complete enough to plan from.

	Refusing is the point. The failure this replaces was a silent zero deficit
	reading to the operator as "No irrigation required".
	"""
	need = int(settings.get("min_weather_days") or MEASURE_DAYS)
	have = int(agg.get("days") or 0)
	if have >= need:
		return True, ""
	return False, f"{have} of {need} daily weather readings — not enough to plan from"


def demand(agg, carried_mm, settings):
	"""This shift's requirement, before anything is capped."""
	kc = float(settings.get("et_crop_coefficient") or 0.65)
	mm_hr = float(settings.get("default_application_rate_mm_hr") or 0)
	coverage = float(settings.get("default_irrigation_coverage") or 0)

	clean = round(float(agg.get("et_crop_mm") or 0) * kc, 4)
	week_deficit = max(0.0, clean - float(agg.get("rainfall_mm") or 0))
	carried = float(carried_mm or 0)
	total = week_deficit + carried

	if total <= 0 or mm_hr <= 0 or coverage <= 0:
		required = 0.0
	else:
		required = total / mm_hr / (coverage / 100.0)

	reason = ""
	if week_deficit <= 0 and carried <= 0:
		reason = (
			f"Rainfall {round(float(agg.get('rainfall_mm') or 0), 1)} mm meets or exceeds "
			f"Clean ET Crop {round(clean, 1)} mm and no carryover. No irrigation required."
		)

	return {
		"clean_et_crop_mm": clean,
		"week_deficit_mm": round(week_deficit, 4),
		"total_needed_mm": round(total, 4),
		"required_hours": round(required, 4),
		"no_irrigation_reason": reason,
	}


def anthracnose(agg):
	"""Z index and its risk band. Formula and thresholds unchanged."""
	if int(agg.get("temp_days") or 0) <= 0 or float(agg.get("mean_temp") or 0) <= 0:
		return 0.0, "Insufficient Data"

	z = round(
		-58.99 + (3.22 * float(agg["mean_temp"])) + (0.18 * float(agg.get("rainfall_mm") or 0)),
		2,
	)
	if z >= 20:
		band = "High Risk - Fungicide Required"
	elif z >= 15:
		band = "Infection Risk - Monitor Closely"
	elif z >= 5:
		band = "Spore Release - Low Alert"
	else:
		band = "Low Risk"
	return z, band
