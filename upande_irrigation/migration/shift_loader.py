"""Load the shift→block mapping into Irrigation Scheduler.shift_blocks.

The table is denormalised — farm, is_active, rate and coverage are attributes of
the shift but live on every one of its block rows. That is only safe if the rows
of a shift agree, so disagreement is a hard failure rather than a silent pick.

Group warehouses are excluded up front by load_mapping, with a recorded reason, so
a cutover run reports them rather than throwing partway through a save. The child
DocType's own validate() stays wired as the backstop for anything that slips past
that pre-check.
"""

import collections
import json
import os

import frappe

SCHEDULER = "Irrigation Scheduler"
SHIFT_LEVEL = ("farm", "is_active", "application_rate_mm_hr", "irrigation_coverage")

# The four managed sections whose leaf warehouses this mapping is expected to cover.
SECTIONS = ("23HA_SECTION - KL", "56HA_SECTION - KL", "65HA_SECTION - KL", "70HA_SECTION - KL")


def validate_mapping(rows):
	"""Human-readable defects. Empty list means the mapping is internally consistent.

	Scope is deliberately narrow: only whether a single shift's own rows agree with
	each other. Cross-shift observations (two shifts sharing a block, a block used
	nowhere) are not defects by this function's contract — see describe_mapping.
	"""
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


def describe_mapping(rows):
	"""Advisory, non-blocking observations about the mapping. Never gates a load.

	These are cross-shift data-quality notes for a human to weigh in a later
	cleanup pass, not defects: validate_mapping's within-shift-agreement scope is
	deliberately not widened to cover them, and strict mode never fails a load
	because of anything reported here.

	Reports, in this order:
	  - shift pairs whose block sets are identical
	  - blocks that appear in more than one shift
	  - block warehouses under the four managed sections that appear in no shift
	"""
	notes = []
	by_shift = collections.defaultdict(list)
	for r in rows:
		by_shift[r["shift"]].append(r.get("block"))

	block_sets = {shift: frozenset(b for b in blocks if b) for shift, blocks in by_shift.items()}
	shifts_sorted = sorted(block_sets)
	for i, a in enumerate(shifts_sorted):
		for b in shifts_sorted[i + 1 :]:
			if block_sets[a] and block_sets[a] == block_sets[b]:
				notes.append(f"{a} and {b} share the identical block set {sorted(block_sets[a])}")

	block_to_shifts = collections.defaultdict(set)
	for r in rows:
		if r.get("block"):
			block_to_shifts[r["block"]].add(r["shift"])
	for block, shifts_for_block in sorted(block_to_shifts.items()):
		if len(shifts_for_block) > 1:
			notes.append(f"{block} appears in more than one shift — {sorted(shifts_for_block)}")

	present_blocks = {r.get("block") for r in rows if r.get("block")}
	section_children = set()
	for section in SECTIONS:
		section_children.update(
			frappe.get_all("Warehouse", filters={"parent_warehouse": section}, pluck="name")
		)
	for block in sorted(section_children - present_blocks):
		notes.append(f"{block} is a block under a managed section but appears in no shift")

	return notes


def _exclude_group_warehouses(rows):
	"""Split rows into (loadable, excluded). Excluded rows name a group warehouse as
	their block -- the child DocType's validate() rejects these unconditionally, so
	pulling them out here turns a mid-save exception into a reported, named list.
	"""
	loadable, excluded = [], []
	for r in rows:
		block = r.get("block")
		if block and frappe.db.get_value("Warehouse", block, "is_group"):
			excluded.append({
				"shift": r["shift"],
				"block": block,
				"reason": f"{block} is a group warehouse, not a leaf block",
			})
		else:
			loadable.append(r)
	return loadable, excluded


def load_mapping(rows, strict=True):
	"""Replace shift_blocks with these rows. Returns {"loaded", "defects", "excluded", "notes"}.

	Group-warehouse rows are excluded (with a reason, in the returned "excluded"
	list) rather than attempted and left to throw -- this is reporting, not
	repairing: nothing is dropped from the input `rows`, only from what gets
	written to shift_blocks. The child DocType's validate() hook stays in place as
	a backstop for anything that slips past this pre-check.
	"""
	defects = validate_mapping(rows)
	if defects and strict:
		frappe.throw("Shift mapping is inconsistent:\n" + "\n".join(defects))

	loadable, excluded = _exclude_group_warehouses(rows)

	sched = frappe.get_single(SCHEDULER)
	sched.set("shift_blocks", [])
	for r in loadable:
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
	return {
		"loaded": len(loadable),
		"defects": defects,
		"excluded": excluded,
		"notes": describe_mapping(rows),
	}


def load_from_file(path=None, strict=True):
	path = path or os.path.join(os.path.dirname(__file__), "data", "shift_mapping.json")
	with open(path) as fh:
		return load_mapping(json.load(fh), strict=strict)
