"""Tests for the valve state/geometry API after Block Type retirement.

The bug being locked out: api/valves.py joined raw SQL against `tabBlocks List`
to roll a valve's sub-block up to its shift. Task 8 dropped the Blocks List
DocType, but nothing exercised list_states() or geojson(), so the dangling
table reference went unnoticed until a clean install (no leftover physical
table) made it throw outright. These tests call both endpoints for real.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.api.valves import geojson, list_states


class TestListStates(FrappeTestCase):
	def test_it_does_not_raise_and_covers_every_valve(self):
		out = list_states()
		valve_count = frappe.db.count("Tank And Valve", {"asset_type": "Valve"})
		self.assertEqual(len(out["valves"]), valve_count)

	def test_each_row_has_the_documented_shape(self):
		out = list_states()
		row = out["valves"][0]
		for key in (
			"name", "asset_label", "farm", "block", "tank",
			"schedule_state", "manual_state", "effective_state",
			"schedule_planner", "schedule_shift",
			"schedule_started_at", "schedule_ends_at", "next_scheduled_at",
		):
			self.assertIn(key, row)
		self.assertIn(row["schedule_state"], ("ON", "OFF"))

	def test_farm_filter_narrows_the_result(self):
		out = list_states(farm="Lokitela")
		self.assertTrue(out["valves"])
		for v in out["valves"]:
			self.assertEqual(v["farm"], "Lokitela")


class TestGeojson(FrappeTestCase):
	def test_it_does_not_raise_and_returns_a_feature_collection(self):
		out = geojson()
		self.assertEqual(out["type"], "FeatureCollection")
		self.assertIsInstance(out["features"], list)

	def test_every_feature_has_a_geometry(self):
		out = geojson()
		self.assertTrue(out["features"])
		for feat in out["features"]:
			self.assertEqual(feat["type"], "Feature")
			self.assertIn("geometry", feat)
			self.assertTrue(feat["geometry"])
