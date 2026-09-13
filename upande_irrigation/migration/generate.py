"""Write an effective DocType out as a code DocType folder."""

import json
import os

CONTROLLER = '''"""Controller for {name}."""

from frappe.model.document import Document


class {klass}(Document):
	pass
'''


def doctype_folder_name(doctype):
	"""Frappe's scrub(): lowercase, spaces and hyphens to underscores."""
	return doctype.lower().replace(" ", "_").replace("-", "_")


def write_doctype(effective, module_path):
	"""Write <folder>/{json,__init__.py,controller.py}; returns the folder path."""
	folder = doctype_folder_name(effective["name"])
	path = os.path.join(module_path, folder)
	os.makedirs(path, exist_ok=True)

	with open(os.path.join(path, f"{folder}.json"), "w") as fh:
		json.dump(effective, fh, indent=1, sort_keys=True)
		fh.write("\n")

	open(os.path.join(path, "__init__.py"), "a").close()

	# The JSON is regenerated every time -- that's the point of this task. The
	# controller is not: an existing controller.py may carry hand-written
	# validate()/hooks logic (e.g. Tank And Valve's manual_state stamping), and
	# overwriting it would silently delete live business logic. Only write the
	# stub when no controller exists yet.
	controller_path = os.path.join(path, f"{folder}.py")
	if not os.path.exists(controller_path):
		klass = effective["name"].replace(" ", "").replace("-", "")
		with open(controller_path, "w") as fh:
			fh.write(CONTROLLER.format(name=effective["name"], klass=klass))

	return path
