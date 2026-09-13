"""Smoke tests for every whitelisted endpoint against real data.

The bug being locked out: these endpoints are reached only from the dashboard, so
a v16 SQL incompatibility surfaces as an empty panel in a browser rather than a
failing test. Each one is called here with the arguments the dashboard sends.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.api import (
	overview, planner, resources, scheduler, sensors, valves, weather
)

FARM = "Lokitela"


class TestEndpointsDoNotRaise(FrappeTestCase):
	def test_overview_fetch(self):
		self.assertIsNotNone(overview.fetch(farm=FARM))

	def test_planner_fetch(self):
		self.assertIsNotNone(planner.fetch(farm=FARM))

	def test_planner_explain(self):
		name = frappe.db.get_value("Irrigation Planner", {}, "name")
		if not name:
			self.skipTest("no planners on this site")
		self.assertIsNotNone(planner.explain(planner=name))

	def test_resources_fetch(self):
		self.assertIsNotNone(resources.fetch(days=30, farm=FARM))

	def test_sensors_fetch(self):
		self.assertIsNotNone(sensors.fetch(days=30))

	def test_valves_list_states(self):
		self.assertIsNotNone(valves.list_states(farm=FARM))

	def test_valves_geojson(self):
		self.assertIsNotNone(valves.geojson(farm=FARM))

	def test_weather_fetch(self):
		self.assertIsNotNone(weather.fetch(days=30, farm=FARM))

	def test_scheduler_live_sections(self):
		self.assertIsNotNone(scheduler.live_sections(farm=FARM))
