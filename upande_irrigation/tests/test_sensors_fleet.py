"""The IoT fleet overview Meniscus had and the v16 port dropped: every sensor at
once (latest value, Live/Stale, battery/RSSI/SNR), fleet KPIs, and the raw
Recent Readings table.

`Sensor Readings` belongs to upande_sensors, which not every irrigation site
has; fleet() must then return the empty shape, never raise.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.api import sensors

KPI_KEYS = {"sensors", "readings", "avg_battery", "avg_rssi", "online"}
DEVICE_KEYS = {
	"deveui", "sensor_name", "sensor_type", "site_name", "units", "latest_value", "latest_at",
	"stale", "hours_silent", "min_value", "max_value", "battery", "rssi", "snr", "reading_count", "spark",
}


class TestFleet(FrappeTestCase):
	def test_the_shape_is_complete(self):
		out = sensors.fleet(days=30)
		self.assertTrue(KPI_KEYS <= set(out["kpis"]))
		self.assertIsInstance(out["devices"], list)
		self.assertIsInstance(out["readings"], list)
		for d in out["devices"]:
			self.assertTrue(DEVICE_KEYS <= set(d), DEVICE_KEYS - set(d))

	def test_readings_are_capped(self):
		self.assertLessEqual(len(sensors.fleet(days=3650)["readings"]), sensors.FLEET_READINGS_LIMIT)

	def test_a_site_without_sensor_readings_gets_the_empty_shape(self):
		if frappe.db.table_exists("Sensor Readings"):
			self.skipTest("this site has Sensor Readings; covered by the shape test")
		out = sensors.fleet(days=30)
		self.assertTrue(out["meta"]["unavailable"])
		self.assertEqual(out["kpis"]["sensors"], 0)
		self.assertEqual(out["devices"], [])

	def test_bad_days_does_not_raise(self):
		self.assertIn("kpis", sensors.fleet(days="not-a-number"))
