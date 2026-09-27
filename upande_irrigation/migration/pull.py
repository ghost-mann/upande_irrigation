"""Insert exported records while keeping their identity.

insert() would mint a new name and a new creation timestamp. Planner names are
referenced from Irrigation Scheduler Run Shift, and every chart is drawn over
creation, so both are carried across verbatim.

Because names are carried across, `tabSeries` is never advanced by the load —
`getseries()` is never reached. `getseries()` has no existence check: a missing
counter row silently restarts at 1, so the first record created after a clean
migration collides with a migrated name. `reseed_series()` closes that hole and
runs at the end of `load_all`.

The two Singles (Irrigation Settings, Irrigation Scheduler) hold configuration
that the code DocType defaults do NOT reproduce, so they are exported and loaded
too — see `SINGLES`.
"""

import json
import os
import re

import frappe
from frappe.model import table_fields


def assert_complete(doctype, records):
	"""Raise unless every record carries a value for every reqd field of `doctype`.

	`ignore_mandatory=True` used to be applied blanket to every record of every
	doctype, so a genuinely short export row would have loaded silently. This
	path runs against production data, so an incomplete record must stop the
	load — and stop it before anything is inserted, not 5000 rows in.
	"""
	allowed = NULLABLE_LEGACY.get(doctype, set())
	required = [
		df.fieldname
		for df in frappe.get_meta(doctype).fields
		if df.reqd and df.fieldname not in allowed
	]
	if not required:
		return

	for record in records:
		missing = [
			fieldname
			for fieldname in required
			if record.get(fieldname) is None
			or (isinstance(record.get(fieldname), str) and not record[fieldname].strip())
		]
		if missing:
			raise frappe.ValidationError(
				f"{doctype} {record.get('name')}: export is missing required "
				f"field(s) {', '.join(missing)}. Fix the export, or add the "
				f"fieldname to pull.NULLABLE_LEGACY with the reason it is "
				f"legitimately blank — do not load it blind."
			)


# Doctype -> fieldnames that are reqd today but are legitimately blank on legacy
# rows. Empty as of this export: every reqd field is populated on every record
# in migration/data. Add an entry (with the reason) only when a real export row
# proves a field cannot be back-filled — never to silence a short export.
NULLABLE_LEGACY = {}


def insert_preserving(doctype, records):
	"""Insert records keeping name/creation/owner/modified. Returns how many were inserted.

	doc.flags.ignore_validate suppresses validate and before_save only. It does NOT
	suppress: before_validate, before_insert, after_insert, on_update, or on_change.

	This function is safe ONLY while none of the migrated doctypes
	(Tank And Valve, Weather Reading, Irrigation Planner, Reservoir Pumping Record,
	Water Transfer, Irrigation Scheduler Run, Water Meter Reading, Electricity
	Meter Reading) define or register hooks for those
	unsuppressed entry points. If a hook is added to any of them, imported values
	will silently corrupt without warning (as happened to et_pan: 2.5 → 17.2 when
	validate ran during migration). Add one before making changes to this pattern.
	"""
	assert_complete(doctype, records)

	inserted = 0
	for record in records:
		if frappe.db.exists(doctype, record["name"]):
			continue

		doc = frappe.get_doc({**record, "doctype": doctype})
		doc.flags.name_set = True
		doc.flags.ignore_validate = True
		doc.insert(
			ignore_permissions=True,
			set_name=record["name"],
			ignore_links=True,
			# Mandatory presence is asserted above, per record, against this
			# site's own meta. This flag now only stops Frappe re-deciding
			# mandatoriness from depends_on expressions mid-import.
			ignore_mandatory=True,
		)

		# `modified` matters as much as `creation`: every migrated DocType sets
		# sort_field = "modified", so without this every list view orders by
		# import order instead of real history.
		stamps = {
			key: record.get(key)
			for key in ("creation", "owner", "modified", "modified_by")
			if record.get(key)
		}
		if stamps:
			frappe.db.set_value(doctype, record["name"], stamps, update_modified=False)
		inserted += 1

	frappe.db.commit()
	return inserted


# Load order matters: Tank And Valve and Weather Reading are referenced by the
# records that follow, and Irrigation Scheduler Run Shift rows name planners.
LOAD_ORDER = (
	("Tank And Valve", "tank_and_valve.json"),
	("Weather Reading", "weather_reading.json"),
	("Irrigation Planner", "irrigation_planner.json"),
	("Reservoir Pumping Record", "reservoir_pumping_record.json"),
	("Water Transfer", "water_transfer.json"),
	("Irrigation Scheduler Run", "irrigation_scheduler_run.json"),
	# Adopted from the retired upande_kaitet (2026-09-27). Both link Warehouse
	# and Irrigation Planner, so they load after the planners.
	("Water Meter Reading", "water_meter_reading.json"),
	("Electricity Meter Reading", "electricity_meter_reading.json"),
)

# Informational only — the volumes the 2026-09-13 export happened to carry.
# `ok` is derived from len(records), never from these: Weather Reading keeps
# accruing on the source, so a cutover export taken later is larger and still
# correct. A mismatch here is a note to read, not a failure.
COUNTS_AT_FIRST_EXPORT = {
	"Tank And Valve": 58,
	"Weather Reading": 5431,
	"Irrigation Planner": 1346,
	"Reservoir Pumping Record": 369,
	"Water Transfer": 279,
	"Irrigation Scheduler Run": 32,
	"Water Meter Reading": 82,         # counted 2026-09-27
	"Electricity Meter Reading": 310,  # counted 2026-09-27
}

# The two Singles. `preserve` names child tables/fields the load must NOT touch:
# Irrigation Scheduler.shift_blocks holds the 86 shift→warehouse mapping rows
# built by the shift loader, and the source's own copy is the pre-migration one.
SINGLES = (
	("Irrigation Settings", "irrigation_settings.json", ()),
	("Irrigation Scheduler", "irrigation_scheduler.json", ("shift_blocks",)),
)

_SINGLE_META_FIELDS = {
	"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx", "doctype",
}

_TRAILING_DIGITS = re.compile(r"^(?P<prefix>.*?)(?P<digits>\d+)$")


def series_targets(doctype, records):
	"""{tabSeries key: highest suffix} implied by these records' names.

	Derived from the data and from this site's meta — never hardcoded, because a
	later cutover export carries different maxima.

	Two counter shapes exist among the migrated doctypes:

	* `IRPL-.YYYY.-.#####` and `naming_series:` build the counter key out of the
	  literal part of the name that precedes the digits (`IRPL-2026-`), so the
	  key can be read straight off a migrated name.
	* `format:RPR-{YYYY}-{MM}-{#####}` passes each braced param to
	  `parse_naming_series` on its own, so the digits arrive with an empty
	  prefix — the counter is the site-wide `''` row shared with every other
	  format-named doctype. Its key is `''` regardless of what the name reads.
	"""
	autoname = (frappe.get_meta(doctype).autoname or "").strip()
	if not (autoname.startswith("naming_series:") or "#" in autoname):
		return {}

	shared = autoname.startswith("format:") and "{#" in autoname

	targets = {}
	for record in records:
		match = _TRAILING_DIGITS.match(record.get("name") or "")
		if not match:
			continue
		key = "" if shared else match.group("prefix")
		suffix = int(match.group("digits"))
		if suffix > targets.get(key, 0):
			targets[key] = suffix
	return targets


# Counter keys this app must never write, however its own data maps onto them.
#
# `''` is the counter every `format:` autoname on the site shares — 137 DocTypes
# on this bench alone, currently at 1,999,007. It is not app-private state:
# raising it to this app's Reservoir Pumping Record maximum (357,190) would be a
# no-op here but would renumber 137 unrelated doctypes on any destination whose
# counter sits lower, irreversibly, on a live ERP with 459 users.
#
# And it buys nothing. Reservoir Pumping Record names embed the month
# (`format:RPR-{YYYY}-{MM}-{#####}` → `RPR-2026-04-345389`), so a counter that
# restarts at 1 can only collide inside the same calendar month as a migrated
# record. The migrated rows are all `RPR-2026-04-`; a cutover in any later month
# cannot collide no matter where the counter sits. There is no safe *and* useful
# write to make here, so this app makes none.
#
# The other two accumulated keys, `IRPL-2026-` and `IRSR-2026-`, are the literal
# prefixes of this app's own series templates and are private to it — no other
# doctype can produce them. Those are reseeded.
SHARED_COUNTER_KEYS = {""}


def reseed_series(targets):
	"""Raise each app-private counter in `targets` to at least the given value.

	Two things this never does: lower a counter (a counter ahead of the data is
	left alone), and touch a key in SHARED_COUNTER_KEYS at all.
	"""
	from frappe.model.naming import NamingSeries

	report = {}
	for key, wanted in sorted(targets.items()):
		series = NamingSeries(key)
		current = series.get_current_value()

		if key in SHARED_COUNTER_KEYS:
			report[key] = {
				"was": current, "now": current, "wanted": wanted,
				"action": "skipped: shared site-wide counter, not this app's to move",
			}
			continue

		if current < wanted:
			series.update_counter(wanted)
			report[key] = {"was": current, "now": wanted, "action": "reseeded"}
		else:
			report[key] = {"was": current, "now": current, "action": "already ahead"}

	frappe.db.commit()
	return report


def load_single(doctype, payload, preserve=()):
	"""Write an exported Single's scalar fields onto this site's copy.

	Fields absent from this site's meta are reported and skipped rather than
	written, so an export taken before a field was added does not fail the load.
	Child tables are never written — see SINGLES.
	"""
	meta = frappe.get_meta(doctype)
	applied, skipped = {}, []
	for field, value in payload.items():
		if field in _SINGLE_META_FIELDS:
			continue
		if field in preserve:
			skipped.append(f"{field}: preserved, not overwritten")
			continue
		df = meta.get_field(field)
		if not df:
			skipped.append(f"{field}: not on this site")
			continue
		if df.fieldtype in table_fields:
			skipped.append(f"{field}: child table, not overwritten")
			continue
		frappe.db.set_single_value(doctype, field, value, update_modified=False)
		applied[field] = value

	frappe.db.commit()
	frappe.clear_document_cache(doctype, doctype)
	return {"applied": applied, "skipped": skipped}


def load_singles(data_dir=None):
	"""Load both exported Singles. Returns a per-doctype report."""
	data_dir = data_dir or os.path.join(os.path.dirname(__file__), "data")
	report = {}
	for doctype, filename, preserve in SINGLES:
		path = os.path.join(data_dir, filename)
		if not os.path.exists(path):
			report[doctype] = f"MISSING {filename}"
			continue
		with open(path) as fh:
			payload = json.load(fh)
		report[doctype] = load_single(doctype, payload, preserve)
	return report


def count_present(doctype, records):
	"""How many of these records' names are already on this site.

	Counted so the load's `ok` can require `inserted + already_present` to
	account for every row in the file. `frappe.db.count(doctype)` cannot: it is
	site-wide, so on a destination that already holds rows of these doctypes a
	load that inserted nothing at all would still clear a `>= len(records)`
	check. Batched rather than one exists() per row.
	"""
	names = [record["name"] for record in records]
	present = 0
	for start in range(0, len(names), 500):
		chunk = names[start:start + 500]
		present += len(frappe.get_all(doctype, filters={"name": ("in", chunk)}, pluck="name"))
	return present


def load_all(data_dir=None):
	"""Load every exported file in dependency order. Returns per-doctype counts."""
	data_dir = data_dir or os.path.join(os.path.dirname(__file__), "data")
	report = {}
	targets = {}
	for doctype, filename in LOAD_ORDER:
		path = os.path.join(data_dir, filename)
		if not os.path.exists(path):
			report[doctype] = f"MISSING {filename}"
			continue
		with open(path) as fh:
			records = json.load(fh)
		already_present = count_present(doctype, records)
		inserted = insert_preserving(doctype, records)
		for key, suffix in series_targets(doctype, records).items():
			if suffix > targets.get(key, 0):
				targets[key] = suffix
		# The gate is per-file, not site-wide: every row in the file must be
		# accounted for by this load or by a previous one. `now_on_site` is
		# reported alongside but is information, never the check -- it counts
		# rows this file never contained.
		report[doctype] = {
			"in_file": len(records),
			"already_present": already_present,
			"inserted": inserted,
			"accounted_for": already_present + inserted,
			"now_on_site": frappe.db.count(doctype),
			"ok": already_present + inserted == len(records),
			"at_first_export": COUNTS_AT_FIRST_EXPORT.get(doctype),
		}

	report["_singles"] = load_singles(data_dir)
	# Last: the counters can only be reseeded once every name is on the site.
	report["_series"] = reseed_series(targets)
	print(json.dumps(report, indent=1, default=str))
	return report
