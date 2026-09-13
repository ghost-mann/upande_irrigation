"""Shared helpers for this app's tests.

ROW_META_KEYS: keys `as_dict()` adds to a child-table row that must be stripped
before the row is fed back into `append()` to restore it. Chief among them is
`name` — if it survives into the dict being appended, Frappe treats the row as an
*update* to an existing row rather than a fresh insert, and if the row it would be
updating was already deleted (e.g. by a preceding `set("shift_blocks", [])`), that
update silently affects nothing. See test_shifts.py's TestShiftLookup for the bug
this caused: restoring a snapshot taken with plain `as_dict()` quietly zeroed out
Irrigation Scheduler.shift_blocks instead of restoring it.
"""

ROW_META_KEYS = (
	"name", "creation", "modified", "modified_by", "owner",
	"parent", "parentfield", "parenttype", "doctype", "idx",
)
