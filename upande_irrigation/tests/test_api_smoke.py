"""Smoke tests for every whitelisted endpoint against real data.

The bug being locked out: these endpoints are reached only from the dashboard, so
a v16 SQL incompatibility surfaces as an empty panel in a browser rather than a
failing test. Each one is called here with the arguments the dashboard sends.

A second, narrower bug is locked out for `overview.fetch`: its sub-queries are
each wrapped in a guard (`api/overview.py`'s `_guard`) that catches any
exception, logs it to Error Log, and returns a default instead of raising. That
is the right behaviour for production (one broken tile should not blank the
whole dashboard) but it means `assertIsNotNone(overview.fetch(...))` alone
proves nothing — a broken guarded query is indistinguishable from a working
one, since both produce a non-None result. `weather.fetch` has the same shape
of guard around its irrometer-block lookup, except it surfaces the swallowed
exception directly as `blocks_error` in the response rather than only logging
it, so that field is asserted directly instead.

`sensors.fetch` now has the same shape of guard as `overview.fetch`: every one
of its queries reads `tabSensor Readings`, which is owned by upande_sensors and
is not in every site's app inventory, so the whole call is wrapped in the same
`_guard` and degrades to an empty payload. That makes assertIsNotNone equally
worthless for it, so it asserts the Error Log delta too.

`planner.py`, `resources.py`, `valves.py`, and `scheduler.live_sections` were
checked for the same pattern and do not swallow sub-query exceptions (their only
try/except blocks are TypeError/ValueError guards around parsing the `days`
input) — see task-10-report.md for the module-by-module check.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.api import (
	overview, planner, resources, scheduler, sensors, valves, weather
)

FARM = "Lokitela"


class TestEndpointsDoNotRaise(FrappeTestCase):
	def test_overview_fetch(self):
		"""Assert the Error Log delta, not just a non-None return.

		overview.fetch()'s sub-queries are each wrapped in _guard(), which
		swallows any exception, logs it to Error Log, and substitutes a default.
		A v16 SQL break in one of those sub-queries would still let fetch()
		return a non-None dict, so assertIsNotNone alone cannot catch it. The
		delta (not an absolute count) is asserted so a concurrent, unrelated
		error logged elsewhere in the test run cannot make this flaky.
		"""
		before = frappe.db.count("Error Log")
		result = overview.fetch(farm=FARM)
		after = frappe.db.count("Error Log")

		self.assertIsNotNone(result)
		self.assertEqual(
			after - before, 0,
			"overview.fetch swallowed a sub-query exception into Error Log "
			"instead of the query actually succeeding",
		)

		self.assertIn("tiles", result)
		self.assertIsInstance(result["tiles"], list)
		self.assertIn("alerts", result)
		self.assertIsInstance(result["alerts"], list)
		self.assertIn("week", result)
		self.assertIsInstance(result["week"], dict)
		self.assertIn("planners", result["week"])

	def test_planner_fetch(self):
		result = planner.fetch(farm=FARM)
		self.assertIsNotNone(result)
		self.assertIsInstance(result.get("week"), dict)
		self.assertIsInstance(result.get("weeks"), list)
		self.assertIsInstance(result.get("days"), list)
		self.assertIsInstance(result.get("gantt"), dict)
		self.assertIsInstance(result["gantt"].get("sections"), list)
		self.assertIsInstance(result.get("pump_load"), dict)
		self.assertIsInstance(result["pump_load"].get("pumps"), list)
		self.assertIsInstance(result.get("balance"), dict)

	def test_planner_explain(self):
		name = frappe.db.get_value("Irrigation Planner", {}, "name")
		if not name:
			self.skipTest("no planners on this site")
		result = planner.explain(planner=name)
		self.assertIsNotNone(result)
		self.assertEqual(result.get("planner"), name)
		self.assertIsInstance(result.get("steps"), list)
		self.assertGreater(len(result["steps"]), 0)

	def test_resources_fetch(self):
		result = resources.fetch(days=30, farm=FARM)
		self.assertIsNotNone(result)
		self.assertIsInstance(result.get("sections"), list)
		self.assertIsInstance(result.get("profiles"), dict)
		self.assertIsInstance(result.get("water"), dict)
		self.assertIsInstance(result.get("electricity"), dict)
		self.assertIsInstance(result.get("pumping"), dict)
		self.assertIsInstance(result.get("kpis"), dict)

	def test_sensors_fetch(self):
		"""Assert the Error Log delta, not just a non-None return.

		sensors.fetch is wrapped in overview's _guard so a site without
		upande_sensors gets an empty Sensors view instead of a dead dashboard.
		On THIS site `Sensor Readings` exists, so the guard must not fire: a
		swallowed v16 SQL break would otherwise be indistinguishable from a
		working query, both returning a non-None dict.
		"""
		before = frappe.db.count("Error Log")
		result = sensors.fetch(days=30)
		after = frappe.db.count("Error Log")
		self.assertEqual(
			after - before, 0,
			"sensors.fetch swallowed an exception into Error Log instead of "
			"the query actually succeeding",
		)
		self.assertIsNotNone(result)
		self.assertNotIn("unavailable", result.get("meta", {}))
		self.assertIsInstance(result.get("sites"), list)
		self.assertIsInstance(result.get("types"), list)
		self.assertIsInstance(result.get("devices"), list)
		self.assertIsInstance(result.get("series"), dict)
		# `stats` is legitimately None here — it is only populated when a single
		# device (deveui) is selected, which this call does not do.

	def test_valves_list_states(self):
		result = valves.list_states(farm=FARM)
		self.assertIsNotNone(result)
		self.assertIsInstance(result.get("valves"), list)
		self.assertIsInstance(result.get("tanks"), list)

	def test_valves_geojson(self):
		result = valves.geojson(farm=FARM)
		self.assertIsNotNone(result)
		self.assertEqual(result.get("type"), "FeatureCollection")
		self.assertIsInstance(result.get("features"), list)

	def test_weather_fetch(self):
		"""Do not assert the `weather` list is non-empty.

		The newest real Weather Reading on this site is over 50 days old (see
		overview's own "missing readings" alert), so a 30-day window legitimately
		returns zero rows. Asserting non-empty content here would be a time bomb
		that fires as the data ages further rather than a real regression check.

		`blocks_error` is asserted instead: it is where weather.fetch's own
		guard (a try/except around the irrometer-block lookup) surfaces a
		swallowed exception, so a v16 break there is still caught even though
		fetch() itself never raises.
		"""
		result = weather.fetch(days=30, farm=FARM)
		self.assertIsNotNone(result)
		self.assertIsNone(result.get("blocks_error"))
		self.assertIsInstance(result.get("weather"), list)
		self.assertIsInstance(result.get("blocks"), list)
		self.assertIsInstance(result.get("kpis"), dict)

	def test_scheduler_live_sections(self):
		result = scheduler.live_sections(farm=FARM)
		self.assertIsNotNone(result)
		self.assertIsInstance(result.get("farms"), list)


class TestSensorsDegradesInsteadOfRaising(FrappeTestCase):
	"""The bug being locked out: every query in api/sensors.py reads
	`tabSensor Readings`, owned by upande_sensors -- an app that is not in the
	destination site's inventory. Unguarded, the first query raises `Table
	doesn't exist` and takes the whole dashboard page down. api/overview.py's
	_guard docstring already anticipated exactly this case; sensors had no
	equivalent.
	"""

	def test_a_failing_query_returns_the_empty_payload(self):
		from unittest import mock

		with mock.patch.object(sensors, "_fetch", side_effect=Exception("Table doesn't exist")):
			result = sensors.fetch(days=30)

		self.assertTrue(result["meta"]["unavailable"])
		self.assertEqual(result["sites"], [])
		self.assertEqual(result["types"], [])
		self.assertEqual(result["devices"], [])
		self.assertIsNone(result["stats"])
		self.assertEqual(result["series"], {"labels": [], "avg": [], "min": [], "max": []})
