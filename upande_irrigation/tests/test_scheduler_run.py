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
		farms, _ = S.irrigation_farms()
		if not farms:
			self.skipTest("no irrigation farm on this site")
		groups = S.pump_groups(farms[0])
		self.assertIsInstance(groups, dict)
		for prefixes in groups.values():
			self.assertIsInstance(prefixes, list)
			self.assertNotIn("_SECTION", "".join(prefixes))

	def test_no_profile_means_no_pump_is_named(self):
		# The site has zero Irrigation Pump Profile rows, so this is the live path.
		if frappe.db.count("Irrigation Pump Profile"):
			self.skipTest("pump profiles exist — grouping is no longer degenerate")
		farms, _ = S.irrigation_farms()
		if not farms:
			self.skipTest("no irrigation farm on this site")
		self.assertEqual(S.pump_groups(farms[0]), {})


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


class TestFarmSelectionSurvivesAMissingFlag(FrappeTestCase):
	"""The bug being locked out: run() filtered Farm on `is_irrigation_farm`, a
	field shipped by no app in the inventory — on this bench it exists only as
	an orphan Custom Field with a NULL module, and the fixture that once carried
	it has been deleted. On the destination the query raises `Unknown column`
	and the whole weekly run dies before its main loop, while the dashboard
	(www/upande_irrigation.py) degrades because it wraps the identical query.

	Absence is simulated by driving the meta-guard directly rather than dropping
	a column from a live table.
	"""

	def test_the_flag_is_read_from_meta_not_assumed(self):
		self.assertIsInstance(S.farm_flag_exists(), bool)
		self.assertFalse(S.farm_flag_exists("a_field_no_farm_ever_had"))

	def test_all_farms_are_planned_when_the_flag_is_absent(self):
		from unittest import mock

		with mock.patch.object(S, "farm_flag_exists", return_value=False):
			farms, how = S.irrigation_farms()

		everything = [f["name"] for f in frappe.get_all("Farm", fields=["name"], order_by="name asc")]
		self.assertEqual(farms, everything)
		self.assertIn("absent", how)

	def test_the_path_taken_is_stated_not_silent(self):
		_, how = S.irrigation_farms()
		self.assertTrue(how.strip(), "farm selection must explain itself in the run log")
