"""Insert exported records while keeping their identity.

insert() would mint a new name and a new creation timestamp. Planner names are
referenced from Irrigation Scheduler Run Shift, and every chart is drawn over
creation, so both are carried across verbatim.
"""

import json
import os

import frappe


def insert_preserving(doctype, records):
	"""Insert records keeping name/creation/owner. Returns how many were inserted."""
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
			ignore_mandatory=True,
		)

		frappe.db.set_value(
			doctype, record["name"],
			{"creation": record.get("creation"), "owner": record.get("owner")},
			update_modified=False,
		)
		inserted += 1

	frappe.db.commit()
	return inserted


# Load order matters: Tank And Valve and Weather Reading are referenced by the
# records that follow, and Irrigation Scheduler Run Shift rows name planners.
LOAD_ORDER = (
	("Tank And Valve", "tank_and_valve.json", 58),
	("Weather Reading", "weather_reading.json", 5431),
	("Irrigation Planner", "irrigation_planner.json", 1346),
	("Reservoir Pumping Record", "reservoir_pumping_record.json", 369),
	("Water Transfer", "water_transfer.json", 279),
	("Irrigation Scheduler Run", "irrigation_scheduler_run.json", 32),
)


def load_all(data_dir=None):
	"""Load every exported file in dependency order. Returns per-doctype counts."""
	data_dir = data_dir or os.path.join(os.path.dirname(__file__), "data")
	report = {}
	for doctype, filename, expected in LOAD_ORDER:
		path = os.path.join(data_dir, filename)
		if not os.path.exists(path):
			report[doctype] = f"MISSING {filename}"
			continue
		with open(path) as fh:
			records = json.load(fh)
		inserted = insert_preserving(doctype, records)
		total = frappe.db.count(doctype)
		report[doctype] = {
			"in_file": len(records), "inserted": inserted,
			"now_on_site": total, "expected": expected,
			"ok": total == expected,
		}
	print(json.dumps(report, indent=1))
	return report
