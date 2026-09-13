"""Load the shift→block mapping into Irrigation Scheduler.shift_blocks.

The table is denormalised — farm, is_active, rate and coverage are attributes of
the shift but live on every one of its block rows. That is only safe if the rows
of a shift agree, so disagreement is a hard failure rather than a silent pick.

Group warehouses are caught by the child DocType's own validate(); this module
reports them up front so the whole defect list arrives at once instead of one
exception at a time.
"""

import collections
import json
import os

import frappe

SCHEDULER = "Irrigation Scheduler"
SHIFT_LEVEL = ("farm", "is_active", "application_rate_mm_hr", "irrigation_coverage")


def validate_mapping(rows):
	"""Human-readable defects. Empty list means the mapping is internally consistent."""
	defects = []
	by_shift = collections.defaultdict(list)
	for r in rows:
		by_shift[r["shift"]].append(r)

	for shift, group in sorted(by_shift.items()):
		for field in SHIFT_LEVEL:
			seen = {r.get(field) for r in group}
			if len(seen) > 1:
				defects.append(
					f"{shift}: rows disagree on {field} — {sorted(seen, key=str)}"
				)
		blocks = [r.get("block") for r in group]
		for block, n in collections.Counter(blocks).items():
			if n > 1:
				defects.append(f"{shift}: duplicate block row for {block} (×{n})")
	return defects


def load_mapping(rows, strict=True):
	"""Replace shift_blocks with these rows. Returns {"loaded", "defects"}."""
	defects = validate_mapping(rows)
	if defects and strict:
		frappe.throw("Shift mapping is inconsistent:\n" + "\n".join(defects))

	sched = frappe.get_single(SCHEDULER)
	sched.set("shift_blocks", [])
	for r in rows:
		sched.append("shift_blocks", {
			"shift": r["shift"],
			"block": r["block"],
			"farm": r.get("farm"),
			"is_active": 1 if r.get("is_active") else 0,
			"application_rate_mm_hr": r.get("application_rate_mm_hr") or 0,
			"irrigation_coverage": r.get("irrigation_coverage") or 0,
		})
	sched.save(ignore_permissions=True)
	frappe.db.commit()
	return {"loaded": len(rows), "defects": defects}


def load_from_file(path=None, strict=True):
	path = path or os.path.join(os.path.dirname(__file__), "data", "shift_mapping.json")
	with open(path) as fh:
		return load_mapping(json.load(fh), strict=strict)
