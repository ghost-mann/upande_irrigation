"""Turn the fixture pair into one effective DocType definition.

doctype.json holds the DocType as it was exported; custom_field.json holds the
fields six patches added afterwards. The running site is the sum of the two, so
that sum is what a code DocType has to contain.
"""

import copy
import json
import os

SKIP = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx",
        "dt", "doctype", "insert_after", "module"}


def load_fixtures(app_path):
	"""Read doctype.json and custom_field.json from an app's fixtures directory."""
	base = os.path.join(app_path, "fixtures")
	with open(os.path.join(base, "doctype.json")) as fh:
		doctypes = json.load(fh)
	with open(os.path.join(base, "custom_field.json")) as fh:
		custom_fields = json.load(fh)
	return doctypes, custom_fields


def merge_custom_fields(doctype_json, custom_fields):
	"""A copy of doctype_json with its Custom Fields folded into `fields`.

	Each custom field is placed directly after the field named by insert_after.
	An anchor that does not exist appends at the end rather than dropping the
	field — losing a field silently is the failure mode that matters here.
	"""
	out = copy.deepcopy(doctype_json)
	out["custom"] = 0
	fields = out.setdefault("fields", [])

	mine = [cf for cf in custom_fields if cf.get("dt") == doctype_json.get("name")]
	for cf in mine:
		field = {k: v for k, v in cf.items() if k not in SKIP}
		anchor = cf.get("insert_after")
		at = next((i for i, f in enumerate(fields) if f.get("fieldname") == anchor), None)
		if at is None:
			fields.append(field)
		else:
			fields.insert(at + 1, field)
	return out
