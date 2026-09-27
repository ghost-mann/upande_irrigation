"""Bulk valve overrides and farm-scoped map geometry.

Meniscus's "Emergency Stop" and per-section toggles were a UI simulation over a
hardcoded grid. The real control surface only had single-valve set_override, so
closing a section meant fifteen clicks. set_override_bulk is the real version.

The bug being locked out: a bulk call with no scope at all must not quietly mean
"every valve on every farm" — that is one mis-wired button away from forcing
the whole estate. It needs all_valves=1 spelled out.
"""

import datetime
import json

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.api import valves as api
from upande_irrigation.tests.helpers import NoCommit, make_farm, make_warehouse

FARM_A = "_Test Valve Farm A"
FARM_B = "_Test Valve Farm B"


def _valve(label, farm, block=None):
	name = frappe.db.get_value("Tank And Valve", {"asset_label": label})
	if name:
		frappe.db.set_value("Tank And Valve", name, "manual_state", "Auto")
		return name
	return frappe.get_doc({
		"doctype": "Tank And Valve", "asset_type": "Valve", "asset_label": label,
		"farm": farm, "block": block, "manual_state": "Auto",
		"location_geojson": json.dumps({"type": "Point", "coordinates": [34.86, 0.99]}),
	}).insert(ignore_permissions=True).name


class TestSetOverrideBulk(NoCommit, FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_farm(FARM_A)
		make_farm(FARM_B)
		cls.section = make_warehouse("_Test Valve Section", is_group=1, farm=FARM_A)
		cls.block = make_warehouse("_Test Valve Block 1", parent=cls.section, farm=FARM_A)
		cls.a1 = _valve("_TV A1", FARM_A, cls.block)
		cls.a2 = _valve("_TV A2", FARM_A)
		cls.b1 = _valve("_TV B1", FARM_B)

	def setUp(self):
		frappe.set_user("Administrator")
		for v in (self.a1, self.a2, self.b1):
			frappe.db.set_value("Tank And Valve", v, "manual_state", "Auto")

	def state(self, v):
		return frappe.db.get_value("Tank And Valve", v, "manual_state")

	def test_an_explicit_list_is_applied(self):
		out = api.set_override_bulk("Forced Closed", valves=json.dumps([self.a1, self.b1]))
		self.assertEqual(out["count"], 2)
		self.assertEqual(self.state(self.a1), "Forced Closed")
		self.assertEqual(self.state(self.b1), "Forced Closed")
		self.assertEqual(self.state(self.a2), "Auto")

	def test_farm_scope_touches_only_that_farm(self):
		api.set_override_bulk("Forced Open", farm=FARM_A)
		self.assertEqual(self.state(self.a1), "Forced Open")
		self.assertEqual(self.state(self.a2), "Forced Open")
		self.assertEqual(self.state(self.b1), "Auto")

	def test_section_scope_follows_the_block_parent(self):
		out = api.set_override_bulk("Forced Closed", section=self.section)
		self.assertEqual(out["updated"], [self.a1])
		self.assertEqual(self.state(self.a2), "Auto")

	def test_a_lone_valve_string_is_one_valve(self):
		out = api.set_override_bulk("Forced Closed", valves=json.dumps(self.a1))
		self.assertEqual(out["updated"], [self.a1])

	def test_state_changes_are_post_only(self):
		"""A GET that changes a valve can be triggered by a link or an <img>."""
		for fn in (api.set_override, api.set_override_bulk):
			self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[fn], ["POST"], fn.__name__)

	def test_no_scope_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.set_override_bulk("Forced Closed")
		self.assertEqual(self.state(self.b1), "Auto")

	def test_all_valves_must_be_explicit(self):
		out = api.set_override_bulk("Forced Closed", all_valves=1)
		self.assertGreaterEqual(out["count"], 3)
		self.assertEqual(self.state(self.b1), "Forced Closed")

	def test_an_invalid_state_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.set_override_bulk("Off", farm=FARM_A)

	def test_guest_cannot_override(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				api.set_override_bulk("Forced Closed", farm=FARM_A)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(self.state(self.a1), "Auto")


class TestGeometryIsFarmScoped(NoCommit, FrappeTestCase):
	def test_valve_geojson_honours_farm(self):
		make_farm(FARM_A)
		_valve("_TV A1", FARM_A)
		out = api.geojson(farm=FARM_A, asset_type="Valve")
		self.assertTrue(out["features"])
		self.assertTrue(all(f["properties"]["farm"] == FARM_A for f in out["features"]))

	def test_blocks_geojson_never_raises(self):
		"""Warehouse.custom_raw_geojson comes from upande_scp, which a site may not
		have. The map asked for that column directly and lost every block."""
		out = api.blocks_geojson()
		self.assertEqual(out["type"], "FeatureCollection")
		self.assertIsInstance(out["features"], list)
		self.assertIn("unavailable", out["meta"])


class TestBlockInfo(NoCommit, FrappeTestCase):
	"""What the Field Map's block card shows: the block, its shifts, this
	week's plan for them, its valves and its latest irrometer reading."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		self = cls
		make_farm(FARM_A)
		self.section = make_warehouse("_Test Info Section", is_group=1, farm=FARM_A)
		self.block = make_warehouse("_Test Info Block", parent=self.section, farm=FARM_A)
		self.valve = _valve("_TV INFO", FARM_A, self.block)
		frappe.get_doc({
			"doctype": "Irrigation Shift Block", "parent": "Irrigation Scheduler", "parenttype": "Irrigation Scheduler",
			"parentfield": "shift_blocks", "shift": "_TEST - SHIFT 1", "block": self.block, "farm": FARM_A,
			"is_active": 1, "application_rate_mm_hr": 3.5, "irrigation_coverage": 75,
		}).db_insert()
		now = frappe.utils.now_datetime()
		frappe.get_doc({"doctype": "Irrigation Run Sheet", "farm": FARM_A, "date": now.date(), "runs": [{
			"shift": "_TEST - SHIFT 1", "status": "Planned", "planned_hours": 6.5, "cycle_no": 1, "cycles": 1,
			"planned_start": now, "planned_end": now + datetime.timedelta(hours=6.5)}]}).insert(ignore_permissions=True)

	def test_it_describes_the_block(self):
		out = api.block_info(self.block)
		self.assertEqual(out["block"]["section"], self.section)
		self.assertEqual(out["block"]["farm"], FARM_A)
		self.assertEqual([s["shift"] for s in out["shifts"]], ["_TEST - SHIFT 1"])
		self.assertEqual(out["shifts"][0]["application_rate_mm_hr"], 3.5)
		self.assertEqual([v["name"] for v in out["valves"]], [self.valve])
		self.assertEqual(len(out["planners"]), 1)
		self.assertEqual(out["planners"][0]["shift_hours"], 6.5)  # today's run-sheet cycle
		self.assertIn("irrometer", out)

	def test_an_unknown_block_is_refused(self):
		with self.assertRaises(frappe.DoesNotExistError):
			api.block_info("_No Such Block - XX")


class TestValvesFollowTheRunSheet(NoCommit, FrappeTestCase):
	"""The valves' scheduled state now comes from the run sheet, and every manual
	change is logged as a Valve Event — the water balance's strongest record."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_farm(FARM_A)
		cls.section = make_warehouse("_Test VRS Section", is_group=1, farm=FARM_A)
		cls.block = make_warehouse("_Test VRS Block", parent=cls.section, farm=FARM_A)
		cls.valve = _valve("_TV VRS", FARM_A, cls.block)
		frappe.get_doc({"doctype": "Irrigation Shift Block", "parent": "Irrigation Scheduler", "parenttype": "Irrigation Scheduler",
		                "parentfield": "shift_blocks", "shift": "_TVRS - SHIFT 1", "block": cls.block, "farm": FARM_A, "is_active": 1}).db_insert()
		now = frappe.utils.now_datetime()
		frappe.get_doc({"doctype": "Irrigation Run Sheet", "farm": FARM_A, "date": now.date(), "runs": [{
			"shift": "_TVRS - SHIFT 1", "status": "Planned", "planned_hours": 2,
			"planned_start": now - datetime.timedelta(minutes=30), "planned_end": now + datetime.timedelta(minutes=90)}]}).insert(ignore_permissions=True)

	def test_a_valve_is_on_inside_its_planned_cycle(self):
		v = next(x for x in api.list_states(farm=FARM_A)["valves"] if x["name"] == self.valve)
		self.assertEqual(v["schedule_state"], "ON")
		self.assertEqual(v["schedule_shift"], "_TVRS - SHIFT 1")

	def test_an_override_is_logged(self):
		api.set_override(self.valve, "Forced Closed")
		ev = frappe.get_all("Valve Event", filters={"valve": self.valve}, fields=["from_state", "to_state", "source"], order_by="creation desc", limit=1)
		self.assertEqual((ev[0].from_state, ev[0].to_state, ev[0].source), ("Auto", "Forced Closed", "Operator"))

	def test_the_weekly_planner_is_retired(self):
		from upande_irrigation import hooks

		self.assertNotIn("Irrigation Planner", hooks.doc_events)
		self.assertIn("upande_irrigation.scheduled.daily.maybe_run", hooks.scheduler_events["hourly"])
		perms = frappe.get_meta("Irrigation Planner").permissions
		self.assertFalse(any(p.write or p.create for p in perms))
