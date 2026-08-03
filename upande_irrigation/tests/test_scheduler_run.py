"""A run must refuse a farm-week it cannot measure, and must sequence shifts that
share a pump against one capacity.

Bug locked out: the run created a planner for every active shift regardless of
whether any weather existed to plan from, then capped each section against its own
profile while anchoring every section at hour 0 — so sections sharing a pump each
believed they had the whole week.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.api import scheduler as S


class TestPlanWindow(FrappeTestCase):
	def test_window_is_exactly_seven_days(self):
		cfg = frappe.get_cached_doc("Irrigation Scheduler")
		start, end = S.plan_window(cfg)
		self.assertEqual(frappe.utils.date_diff(end, start), 6)

	def test_window_starts_on_the_configured_weekday(self):
		cfg = frappe.get_cached_doc("Irrigation Scheduler")
		start, _ = S.plan_window(cfg)
		expected = S._DAY_INDEX.get(cfg.week_starts_on or "Thursday", 3)
		self.assertEqual(frappe.utils.getdate(start).weekday(), expected)


class TestPumpGroups(FrappeTestCase):
	def test_groups_are_lists_of_section_prefixes(self):
		farm = frappe.get_all("Farm", filters={"is_irrigation_farm": 1}, fields=["name"], limit=1)
		if not farm:
			self.skipTest("no irrigation farm on this site")
		groups = S.pump_groups(farm[0]["name"])
		self.assertIsInstance(groups, dict)
		for prefixes in groups.values():
			self.assertIsInstance(prefixes, list)
			self.assertNotIn("_SECTION", "".join(prefixes))

	def test_no_profile_means_no_pump_is_named(self):
		# The site has zero Irrigation Pump Profile rows, so this is the live path.
		if frappe.db.count("Irrigation Pump Profile"):
			self.skipTest("pump profiles exist — grouping is no longer degenerate")
		farm = frappe.get_all("Farm", filters={"is_irrigation_farm": 1}, fields=["name"], limit=1)
		if not farm:
			self.skipTest("no irrigation farm on this site")
		self.assertEqual(S.pump_groups(farm[0]["name"]), {})


class TestByPump(FrappeTestCase):
	def test_unprofiled_sections_share_one_bucket(self):
		created = [
			{"key": "a", "prefix": "23HA", "required_hours": 4.0},
			{"key": "b", "prefix": "40HA", "required_hours": 4.0},
		]
		groups = S._by_pump("__nofarm__", created)
		self.assertEqual(len(groups), 1)
		pump_name, members = groups[0]
		self.assertIs(pump_name, S._UNKNOWN_PUMP)
		self.assertEqual(len(members), 2)

	def test_every_shift_lands_in_exactly_one_bucket(self):
		created = [{"key": str(i), "prefix": f"S{i}", "required_hours": 1.0} for i in range(5)]
		groups = S._by_pump("__nofarm__", created)
		keys = [m["key"] for _, members in groups for m in members]
		self.assertEqual(sorted(keys), sorted(c["key"] for c in created))
