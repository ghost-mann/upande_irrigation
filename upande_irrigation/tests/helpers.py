"""Test records that need upande_core: every Warehouse requires a Farm there."""

from unittest.mock import patch

import frappe

TEST_FARM = "_Test Irrigation Farm"


def company():
	return frappe.db.get_value("Company", {}, "name")


def make_farm(name=TEST_FARM):
	if not frappe.db.exists("Farm", name):
		frappe.get_doc({
			"doctype": "Farm",
			"farm_name": name,
			"company": company(),
			"abbreviation": "".join(w[0] for w in name.replace("_", " ").split())[:4].upper() or "TF",
			"farm_type": [{"farm_type": "Has Blocks"}],
		}).insert(ignore_permissions=True)
	return name


def make_warehouse(warehouse_name, parent=None, is_group=0, farm=None, **extra):
	"""Return the Warehouse name, creating it (under `parent`) if needed."""
	existing = frappe.db.get_value("Warehouse", {"warehouse_name": warehouse_name}, "name")
	if existing:
		return existing
	doc = frappe.get_doc({
		"doctype": "Warehouse",
		"warehouse_name": warehouse_name,
		"company": company(),
		"is_group": is_group,
		"parent_warehouse": parent,
		"custom_farm": farm or make_farm(),
		**extra,
	})
	doc.insert(ignore_permissions=True)
	return doc.name


class NoCommit:
	"""The code under test commits (set_override_bulk, insert_preserving); without
	this the records these tests create would outlive FrappeTestCase's rollback
	and leak into the site."""

	@classmethod
	def setUpClass(cls):
		cls._no_commit = patch("frappe.db.commit")
		cls._no_commit.start()
		super().setUpClass()

	@classmethod
	def tearDownClass(cls):
		super().tearDownClass()
		cls._no_commit.stop()
