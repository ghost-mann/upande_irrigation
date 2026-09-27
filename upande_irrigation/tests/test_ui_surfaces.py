"""Tests for the desk surfaces.

The bug being locked out: referencing /assets/upande_core/images/ for the logo
breaks the tile on any site without upande_core. The CRM's own hooks comment says
the same thing; here it matters more, because whether upande_core ships alongside
this app on every future site is not guaranteed.
"""

import os

import frappe
from frappe.tests.utils import FrappeTestCase

import upande_irrigation


class TestAppsScreen(FrappeTestCase):
	def test_there_is_one_apps_screen_entry(self):
		from upande_irrigation import hooks
		self.assertEqual(len(hooks.add_to_apps_screen), 1)

	def test_the_logo_is_served_from_this_app(self):
		from upande_irrigation import hooks
		self.assertTrue(hooks.add_to_apps_screen[0]["logo"].startswith("/assets/upande_irrigation/"))

	def test_the_logo_file_actually_exists(self):
		path = os.path.join(os.path.dirname(upande_irrigation.__file__), "public", "images", "upande-logo.png")
		self.assertTrue(os.path.exists(path), f"missing {path}")


class TestWorkspaceSidebar(FrappeTestCase):
	def test_the_sidebar_exists(self):
		self.assertTrue(frappe.db.exists("Workspace Sidebar", "Upande Irrigation"))

	def test_it_links_the_upande_irrigation_workspace(self):
		doc = frappe.get_doc("Workspace Sidebar", "Upande Irrigation")
		targets = {i.link_to for i in doc.items if i.link_type == "Workspace"}
		self.assertIn("Upande Irrigation", targets)

	def test_every_item_has_an_icon(self):
		"""Travel's sidebar carries an icon on every row; a bare row reads as broken."""
		doc = frappe.get_doc("Workspace Sidebar", "Upande Irrigation")
		bare = [i.label for i in doc.items if not i.icon]
		self.assertEqual(bare, [], f"sidebar rows without an icon: {bare}")

	def test_every_doctype_link_resolves(self):
		"""The bug being locked out: a sidebar item pointing at a DocType that is
		not installed renders as a live link that 404s for every user. This branch
		already had to clean one such link up, and only the single Workspace-type
		link was ever asserted.
		"""
		doc = frappe.get_doc("Workspace Sidebar", "Upande Irrigation")
		dangling = [
			item.link_to
			for item in doc.items
			if item.type == "Link"
			and item.link_type == "DocType"
			and not frappe.db.exists("DocType", item.link_to)
		]
		self.assertEqual(dangling, [], f"sidebar links to missing DocType(s): {dangling}")
