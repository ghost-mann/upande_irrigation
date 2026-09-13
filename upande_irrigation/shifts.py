"""Shift lookup, reading the Irrigation Scheduler's shift_blocks table.

Replaces the Block Type queries. A shift is one row per block, so the shift-level
columns repeat across a shift's rows; every reader here takes the first row of a
shift and ignores the rest, which is safe because the loader refuses to import a
shift whose rows disagree.
"""

import frappe

SCHEDULER = "Irrigation Scheduler"
SEPARATOR = " - SHIFT "


def _rows():
	return frappe.get_single(SCHEDULER).shift_blocks or []


def _shift_number(shift):
	"""Trailing integer of '70HA - SHIFT 20'. Block Type sorted on this, so we do too."""
	tail = (shift or "").rsplit(SEPARATOR, 1)[-1]
	try:
		return int(tail)
	except (TypeError, ValueError):
		return 0


def active_shifts(farm, prefix):
	"""Active shift names for one section prefix, in irrigation order."""
	wanted = f"{prefix}{SEPARATOR}"
	names = {
		r.shift
		for r in _rows()
		if r.is_active and r.farm == farm and (r.shift or "").startswith(wanted)
	}
	return sorted(names, key=lambda s: (_shift_number(s), s))


def count_active_shifts(farm, prefix):
	return len(active_shifts(farm, prefix))


def shift_overrides(shift):
	"""This shift's rate and coverage. Zero means inherit the farm default."""
	for r in _rows():
		if r.shift == shift:
			return {
				"application_rate_mm_hr": float(r.application_rate_mm_hr or 0),
				"irrigation_coverage": float(r.irrigation_coverage or 0),
			}
	return {"application_rate_mm_hr": 0.0, "irrigation_coverage": 0.0}


def blocks_of(shift):
	"""Block warehouse names this shift waters."""
	return [r.block for r in _rows() if r.shift == shift and r.block]
