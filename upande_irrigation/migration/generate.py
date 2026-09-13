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

	klass = effective["name"].replace(" ", "").replace("-", "")
	with open(os.path.join(path, f"{folder}.py"), "w") as fh:
		fh.write(CONTROLLER.format(name=effective["name"], klass=klass))

	return path
