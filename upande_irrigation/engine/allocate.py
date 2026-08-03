"""Turning a set of requirements into granted hours and time windows.

This is the part a per-document hook could never do. Capping depends on what else
draws on the pump, and cycle placement depends on what else runs that day, so both
need to see every shift at once. Pure, so both are testable without a database.
"""

import datetime
import math

FULL_WEEK_HOURS = 168.0
WEEK_DAYS = 7


def pump_capacity_hours(profile):
	"""Weekly hours the pump can run, and whether that figure is real.

	With no Irrigation Pump Profile there is no capacity to allocate against, so
	the caller gets a full week and an explicit "unverified" — never a guess
	dressed up as a limit. The site currently has zero profiles, so this is the
	live path, and the distinction is the whole point.
	"""
	if profile:
		target = float(profile.get("water_target_m3_per_week") or 0)
		flow = float(profile.get("pump_flow_rate_m3_per_hr") or 0)
		if target > 0 and flow > 0:
			return round(target / flow, 4), True
	return FULL_WEEK_HOURS, False


def grant(requests, capacity_hours):
	"""Scale every request by one factor so shares stay proportional to need.

	The old rule was capacity ÷ N, which handed a shift with no demand the same
	slice as a parched one and then threw the unused slice away.
	"""
	out = []
	total = sum(max(0.0, float(r.get("required_hours") or 0)) for r in requests)
	factor = 1.0
	if capacity_hours > 0 and total > capacity_hours:
		factor = capacity_hours / total

	for r in requests:
		need = max(0.0, float(r.get("required_hours") or 0))
		out.append(
			{
				**r,
				"granted_hours": round(need * factor, 4),
				"capped": factor < 1.0 and need > 0,
			}
		)
	return out


def cycle_plan(hours, settings):
	"""Split a run so no single cycle exceeds the threshold. Formula unchanged."""
	threshold = float(settings.get("auto_cycle_threshold_hrs") or 5.0)
	below = max(1, int(settings.get("cycles_when_below_threshold") or 1))
	above = max(1, int(settings.get("cycles_when_above_threshold") or 2))

	if hours <= 0:
		return 0, 0.0
	if hours <= threshold:
		count = below
	else:
		by_cap = math.ceil(hours / threshold) if threshold > 0 else above
		count = max(by_cap, above)
	return count, round(hours / count, 4)


def lay_out(granted, plan_from, settings):
	"""Place each shift's cycles in the week without overlapping.

	One queue per call, so callers group by pump before calling: two shifts drawing
	on one pump cannot run at the same instant. Cycles of a shift are separated by
	cycle_rest_hours, so water has time to infiltrate — back-to-back cycles, which
	is what the old engine produced, are not cycling at all.
	"""
	rest = float(settings.get("cycle_rest_hours") or 0)
	week_start = datetime.datetime.combine(plan_from, datetime.time.min)
	week_end = week_start + datetime.timedelta(days=WEEK_DAYS)

	cursor = week_start
	out = []
	for r in granted:
		hours = float(r.get("granted_hours") or 0)
		count, each = cycle_plan(hours, settings)
		windows = []
		for i in range(count):
			start = cursor
			end = start + datetime.timedelta(hours=each)
			if end > week_end:
				# The week is full. Anything unplaced stays unmet and carries, which
				# is more honest than spilling into next week's plan.
				break
			windows.append((start, end))
			cursor = end + datetime.timedelta(hours=rest if i < count - 1 else 0)
		placed = round(each * len(windows), 4)
		out.append(
			{
				**r,
				"cycles_count": len(windows),
				"cycle_hours_each": each if windows else 0.0,
				"placed_hours": placed,
				"windows": windows,
			}
		)
	return out
