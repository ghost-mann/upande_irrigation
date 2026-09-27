"""The desk shell follows upande_travel: a standard Desktop Icon that opens the
sidebar, a workspace whose body is one Custom HTML tile grid, and no leftover
v15 "Smart Irrigation" workspace or auto-generated sidebar.

The bug being fixed: the Apps-screen tile was an ad-hoc `standard: 0` row with
an External link to /app/smart-irrigation, so it was not refreshed on migrate
and did not show as the Upande app.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation import setup


class TestDeskShell(FrappeTestCase):
	def setUp(self):
		setup.after_migrate()

	def test_desktop_icon_is_standard_and_opens_the_sidebar(self):
		icon = frappe.get_doc("Desktop Icon", setup.DESKTOP_ICON)
		self.assertEqual(icon.standard, 1)
		self.assertEqual(icon.icon_type, "App")
		self.assertEqual(icon.app, "upande_irrigation")
		self.assertEqual(icon.link_type, "Workspace Sidebar")
		self.assertEqual(icon.link_to, "Upande Irrigation")
		self.assertEqual(icon.logo_url, "/assets/upande_irrigation/images/upande-logo.png")

	def test_a_user_hidden_tile_stays_hidden_across_migrate(self):
		frappe.db.set_value("Desktop Icon", setup.DESKTOP_ICON, "hidden", 1)
		setup.after_migrate()
		self.assertEqual(frappe.db.get_value("Desktop Icon", setup.DESKTOP_ICON, "hidden"), 1)

	def test_the_workspace_body_is_the_navigation_block(self):
		self.assertTrue(frappe.db.exists("Custom HTML Block", setup.NAVIGATION_BLOCK))
		block = frappe.get_doc("Custom HTML Block", setup.NAVIGATION_BLOCK)
		self.assertIn("/upande-irrigation", block.html)
		ws = frappe.get_doc("Workspace", "Upande Irrigation")
		self.assertIn(setup.NAVIGATION_BLOCK, ws.content)

	def test_every_navigation_tile_doctype_exists(self):
		import re

		block = frappe.get_doc("Custom HTML Block", setup.NAVIGATION_BLOCK)
		missing = [d for d in re.findall(r'data-doctype="([^"]+)"', block.html) if not frappe.db.exists("DocType", d)]
		self.assertEqual(missing, [], f"navigation tiles for missing DocType(s): {missing}")

	def test_the_legacy_desk_is_gone(self):
		self.assertFalse(frappe.db.exists("Workspace", "Smart Irrigation"))
		self.assertFalse(frappe.db.exists("Workspace Sidebar", "Smart Irrigation"))

	def test_after_migrate_is_idempotent(self):
		setup.after_migrate()
		setup.after_migrate()
		self.assertEqual(frappe.db.count("Desktop Icon", {"app": "upande_irrigation", "icon_type": "App"}), 1)
