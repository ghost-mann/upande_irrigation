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

	def test_the_shift_join_actually_resolved_rows(self):
		"""A join that returns no rows looks identical to "nothing is scheduled
		right now": every valve still falls through to schedule_state="OFF"
		with null schedule fields, and the shape assertions above cannot tell
		the two apart. sb.shift swapped for sb.block, a misspelled column, or
		a parenttype/parentfield constraint that excludes everything would
		all leave list_states() looking healthy while reporting nothing.

		Assert the join is not empty: on current site data every valve has a
		next_scheduled_at, so at least one non-null value proves rows matched.
		"""
		out = list_states()
		self.assertTrue(
			any(v["next_scheduled_at"] or v["schedule_state"] == "ON" for v in out["valves"]),
			"no valve has a next_scheduled_at or is ON — the shift join returned no rows",
		)


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
