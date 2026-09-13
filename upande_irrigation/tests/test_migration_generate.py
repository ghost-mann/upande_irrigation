"""Tests for code-DocType generation.

The bug being locked out: a generated DocType that still says custom: 1, or that
lands in a folder whose name does not match Frappe's scrub() convention, will not
be picked up by bench migrate — the fixture would keep winning and the conversion
would appear to work while changing nothing.
"""

import json
import os
import tempfile

from frappe.tests.utils import FrappeTestCase

from upande_irrigation.migration.generate import doctype_folder_name, write_doctype


EFFECTIVE = {
	"name": "Irrigation Shift Block",
	"custom": 0,
	"module": "Upande Irrigation",
	"istable": 1,
	"fields": [{"fieldname": "shift", "fieldtype": "Data"}],
}


class TestDoctypeFolderName(FrappeTestCase):
	def test_spaces_become_underscores_and_lowercase(self):
		self.assertEqual(doctype_folder_name("Irrigation Planner"), "irrigation_planner")

	def test_multiple_words(self):
		self.assertEqual(
			doctype_folder_name("Irrigation Scheduler Run Shift"),
			"irrigation_scheduler_run_shift",
		)


class TestWriteDoctype(FrappeTestCase):
	def test_it_writes_json_init_and_controller(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = write_doctype(EFFECTIVE, tmp)
			self.assertTrue(os.path.exists(os.path.join(path, "irrigation_shift_block.json")))
			self.assertTrue(os.path.exists(os.path.join(path, "__init__.py")))
			self.assertTrue(os.path.exists(os.path.join(path, "irrigation_shift_block.py")))

	def test_the_written_json_is_not_custom(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = write_doctype(EFFECTIVE, tmp)
			with open(os.path.join(path, "irrigation_shift_block.json")) as fh:
				self.assertEqual(json.load(fh)["custom"], 0)
