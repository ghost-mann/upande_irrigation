"""The dashboard's farm picker.

upande_core's Farm (what v16 sites run) has no is_irrigation_farm field. The
lookup filtered on it anyway, the query failed, and the picker came back empty
on the live site -- so nobody could log a weather reading.
"""

import frappe
from frappe.tests import IntegrationTestCase

from upande_irrigation.www.upande_irrigation import _irrigation_farms


class TestDashboardFarms(IntegrationTestCase):
	def test_every_farm_is_offered_when_farm_has_no_irrigation_flag(self):
		if frappe.get_meta("Farm").has_field("is_irrigation_farm"):
			self.skipTest("this site's Farm carries the flag")
		farms = _irrigation_farms()
		self.assertEqual(farms, frappe.get_all("Farm", pluck="name", order_by="name asc"))
		self.assertTrue(farms, "the site has farms, so the picker must offer some")
