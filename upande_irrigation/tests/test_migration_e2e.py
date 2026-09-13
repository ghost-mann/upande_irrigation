"""The gate on the production cutover.

Everything below has to hold on real migrated data before the same path is run
against upande.com. A green suite with an empty database proves nothing, so each
assertion names the volume it expects.

Two exceptions to "assert exact volumes", both deliberate:

- `Irrigation Scheduler Run` is asserted as `>= 32`, not `== 32`. This gate's own
  Step 3 runs the live scheduler, which always writes one more run record whether
  or not it plans anything — a fixed exact count and a mandatory live run cannot
  both hold at once. Every other doctype in this file stays an exact equality.
- A scheduler run that attempts zero shifts is not, by itself, a defect. The
  scheduler is designed to refuse to plan and flag when the weather window is
  incomplete, rather than silently returning zero. On this site the newest
  Weather Reading is far older than any live run's planning window, so a
  0-attempt run is the CORRECT, designed behaviour here — and would be a real
  defect if the weather were fresh. Do not "fix" a future 0-attempt run by
  loosening this further or by manufacturing weather data; fix the weather
  data, or accept the gap and say so.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation import shifts
from upande_irrigation.install import APP_ROLES
from upande_irrigation.migration import pull

FARM = "Lokitela"


class TestMigratedSiteIsWhole(FrappeTestCase):
	def test_every_record_arrived(self):
		expected = {
			"Weather Reading": 5431, "Irrigation Planner": 1346,
			"Reservoir Pumping Record": 369, "Water Transfer": 279,
			"Tank And Valve": 58,
		}
		for doctype, count in expected.items():
			self.assertEqual(frappe.db.count(doctype), count, f"{doctype} short")

		# Not an exact match like the five above: this same gate's Step 3 runs
		# the live scheduler, which writes one more Irrigation Scheduler Run
		# record every time it runs, whether or not it plans anything. 32 is
		# the migrated floor, not a ceiling this gate can hold itself to.
		self.assertGreaterEqual(
			frappe.db.count("Irrigation Scheduler Run"), 32, "Irrigation Scheduler Run short"
		)

	def test_the_latest_scheduler_run_is_healthy(self):
		"""A live run must never fail a planner, and must explain a zero-attempt run.

		On this site the newest Weather Reading is 2026-07-24, well outside any
		current planning window, so `shifts_attempted == 0` on the latest run is
		expected and CORRECT — the engine's designed behaviour is to refuse to
		plan and flag when weather data is incomplete, never to silently return
		zero. The same zero on a site with fresh weather would be a real defect.
		This test asserts health (no failures, and a stated reason when nothing
		was attempted), not volume — it must not be loosened further, and a
		future 0-attempt run must not be "fixed" by fabricating weather data.
		"""
		run = frappe.get_last_doc("Irrigation Scheduler Run")
		self.assertIn(run.status, ("Success", "Partial"), run.status)
		self.assertEqual(run.planners_failed, 0)
		if run.shifts_attempted == 0:
			explanation = f"{run.summary or ''} {run.error_summary or ''}".lower()
			self.assertTrue(
				any(word in explanation for word in ("weather", "reading")),
				"a 0-attempt run must say why, not fail silently",
			)

	def test_no_doctype_is_still_custom(self):
		custom = frappe.get_all(
			"DocType", filters={"module": "Upande Irrigation", "custom": 1}, pluck="name"
		)
		self.assertEqual(custom, [])

	def test_the_retired_doctypes_are_gone(self):
		for dt in ("Block Type", "Block configaration", "Blocks List"):
			self.assertFalse(frappe.db.exists("DocType", dt))

	def test_the_only_orphaned_shift_is_the_one_we_excluded(self):
		"""Planners orphaned by the 23HA - SHIFT 1 exclusion are expected, and bounded.

		Both mapping rows for that shift named a group warehouse, so both were
		excluded and the shift no longer exists. Its planners are orphaned by
		design — reported, not repaired. Any OTHER orphan is a real regression.
		"""
		known = {r.shift for r in frappe.get_single("Irrigation Scheduler").shift_blocks}
		used = {b for b in frappe.get_all("Irrigation Planner", pluck="block") if b}
		self.assertEqual(used - known, {"23HA - SHIFT 1"})

	def test_every_shift_block_is_a_real_leaf_warehouse(self):
		for row in frappe.get_single("Irrigation Scheduler").shift_blocks:
			self.assertTrue(frappe.db.exists("Warehouse", row.block), f"{row.block} missing")
			self.assertFalse(
				frappe.db.get_value("Warehouse", row.block, "is_group"),
				f"{row.block} is a group",
			)

	def test_the_four_sections_each_report_active_shifts(self):
		for prefix in ("23HA", "56HA", "65HA", "70HA"):
			self.assertGreater(len(shifts.active_shifts(FARM, prefix)), 0, prefix)


class TestNamingCountersCameAcross(FrappeTestCase):
	"""The bug being locked out: pull.insert_preserving sets flags.name_set and
	passes set_name, so getseries() is never called and tabSeries is never
	advanced. frappe.model.naming.getseries() has no existence check — with no
	counter row it returns 1.

	This bench only survives because it carries tabSeries rows (IRPL-2026- =
	4098, IRSR-2026- = 85) from its own pre-migration history. On a clean
	destination the first scheduler run would be named IRSR-2026-00001 and die
	on a duplicate key, and planner creation would survive 72 records before
	failing from IRPL-2026-00073 — a week-two failure, which is worse than a
	week-one failure. pull.reseed_series() closes that, and this is the guard
	that would have caught it.
	"""

	SERIES_DOCTYPES = (
		"Irrigation Planner",
		"Irrigation Scheduler Run",
		"Reservoir Pumping Record",
	)

	def test_every_counter_clears_the_highest_migrated_name(self):
		for doctype in self.SERIES_DOCTYPES:
			records = [{"name": n} for n in frappe.get_all(doctype, pluck="name")]
			targets = pull.series_targets(doctype, records)
			self.assertTrue(targets, f"{doctype} yielded no series counter to check")
			for key, highest in targets.items():
				# order_by="name": tabSeries has no `creation` column, and
				# get_value's default ORDER BY would raise Unknown column.
				current = frappe.db.get_value("Series", key, "current", order_by="name")
				self.assertIsNotNone(
					current,
					f"{doctype}: tabSeries has no row for {key!r} — the next "
					f"record will be numbered 1 and collide with a migrated name",
				)
				self.assertGreaterEqual(
					int(current), highest,
					f"{doctype}: counter {key!r} is at {current} but names go up "
					f"to {highest} — the next record collides",
				)


class TestTheSinglesCameAcross(FrappeTestCase):
	"""The bug being locked out: pull.LOAD_ORDER covered six doctypes and neither
	Single, so on a clean site Irrigation Settings and Irrigation Scheduler would
	fall back to the code DocType defaults. This bench passed only because it
	already held the source's values.

	default_irrigation_coverage is the sharpest of the three: the code default is
	62.5 and the source is 70, so every planner's required_hours would shift by
	roughly 12% with nothing raising.
	"""

	def test_coverage_is_the_source_value_not_the_code_default(self):
		coverage = frappe.db.get_single_value("Irrigation Settings", "default_irrigation_coverage")
		self.assertEqual(float(coverage), 70.0)
		self.assertNotEqual(float(coverage), 62.5, "fell back to the code DocType default")

	def test_the_scheduler_plans_the_week_the_source_planned(self):
		self.assertEqual(
			frappe.db.get_single_value("Irrigation Scheduler", "plan_for"), "Next Week"
		)



class TestEveryDocTypeIsReachable(FrappeTestCase):
	"""The bug being locked out: every DocPerm this app ships names a Role that no
	app in the inventory creates — ERPNext v16 dropped Agriculture Manager and
	Agriculture User, and Irrigation User was only ever a bare tabRole row on the
	source. DocPerms import with ignore_links=True, so on a clean site they bind
	to nothing and Weather Reading becomes a dead sidebar link for all 459 users,
	System Managers included.
	"""

	APP_DOCTYPES = (
		"Tank And Valve", "Weather Reading", "Irrigation Planner",
		"Reservoir Pumping Record", "Water Transfer", "Irrigation Scheduler Run",
		"Irrigation Settings", "Irrigation Scheduler", "Irrigation Pump Profile",
	)

	def test_the_roles_the_app_depends_on_exist(self):
		missing = [r for r in APP_ROLES if not frappe.db.exists("Role", r)]
		self.assertEqual(missing, [], f"install.ensure_roles did not create: {missing}")

	def test_every_doctype_has_a_docperm_for_a_role_that_exists(self):
		orphaned = {}
		for doctype in self.APP_DOCTYPES:
			roles = frappe.get_all("DocPerm", filters={"parent": doctype}, pluck="role")
			roles += frappe.get_all("Custom DocPerm", filters={"parent": doctype}, pluck="role")
			live = [r for r in roles if r and frappe.db.exists("Role", r)]
			if not live:
				orphaned[doctype] = roles
		self.assertEqual(orphaned, {}, f"no usable DocPerm on: {orphaned}")

	def test_weather_reading_is_reachable_by_a_normal_administrator(self):
		"""Its only DocPerm was Irrigation User, a role no shipped app creates and
		no System Manager automatically holds."""
		roles = frappe.get_all("DocPerm", filters={"parent": "Weather Reading"}, pluck="role")
		self.assertIn("System Manager", roles)


class TestTheShiftMappingSurvivesASingleLoad(FrappeTestCase):
	"""The bug being locked out: load_single() writing Irrigation Scheduler's
	shift_blocks would destroy the 86 shift→warehouse mapping rows built by the
	shift loader in Task 7 — the worst single outcome in this migration, and one
	nothing downstream would report, because a scheduler with an empty mapping
	simply plans nothing.

	This drives the real function with a payload that deliberately CARRIES a
	shift_blocks key, rather than restating pull.SINGLES back at itself.
	"""

	DOCTYPE = "Irrigation Scheduler"
	CHILD = "Irrigation Shift Block"

	def setUp(self):
		self.rows_before = frappe.db.sql(
			f"SELECT * FROM `tab{self.CHILD}` WHERE parent = %s ORDER BY idx",
			self.DOCTYPE,
			as_dict=True,
		)
		self.plan_for_before = frappe.db.get_single_value(self.DOCTYPE, "plan_for")

	def tearDown(self):
		"""Put the mapping back if the thing this test guards against happened."""
		current = frappe.db.count(self.CHILD, {"parent": self.DOCTYPE})
		if current != len(self.rows_before):
			frappe.db.delete(self.CHILD, {"parent": self.DOCTYPE})
			for row in self.rows_before:
				frappe.get_doc({**row, "doctype": self.CHILD}).db_insert()
		frappe.db.set_single_value(
			self.DOCTYPE, "plan_for", self.plan_for_before, update_modified=False
		)
		frappe.db.commit()
		frappe.clear_document_cache(self.DOCTYPE, self.DOCTYPE)

	def _mapping(self):
		return [
			(row.shift, row.block)
			for row in frappe.db.sql(
				f"SELECT shift, block FROM `tab{self.CHILD}` WHERE parent = %s ORDER BY idx",
				self.DOCTYPE,
				as_dict=True,
			)
		]

	def test_a_payload_carrying_shift_blocks_leaves_the_mapping_untouched(self):
		before = self._mapping()
		self.assertEqual(len(before), 86, "precondition: the 86 mapping rows are loaded")

		payload = {
			"name": self.DOCTYPE,
			"doctype": self.DOCTYPE,
			"plan_for": "Next Week",
			# The source's own pre-migration copy, standing in for what a future
			# export that DOES return child tables would hand load_single.
			"shift_blocks": [
				{"shift": "PRE-MIGRATION - SHIFT 1", "block": "NOT A REAL WAREHOUSE"}
			],
		}
		result = pull.load_single(self.DOCTYPE, payload, ("shift_blocks",))

		self.assertEqual(self._mapping(), before, "the shift mapping was overwritten")
		self.assertEqual(len(self._mapping()), 86)
		self.assertNotIn("shift_blocks", result["applied"])
		self.assertTrue(
			any(entry.startswith("shift_blocks") for entry in result["skipped"]),
			f"load_single did not report skipping shift_blocks: {result['skipped']}",
		)

	def test_the_scalar_fields_in_the_same_payload_still_land(self):
		"""Proves the test above is not passing because load_single did nothing."""
		payload = {"name": self.DOCTYPE, "doctype": self.DOCTYPE, "plan_for": "Current Week"}
		pull.load_single(self.DOCTYPE, payload, ("shift_blocks",))
		self.assertEqual(
			frappe.db.get_single_value(self.DOCTYPE, "plan_for"), "Current Week"
		)


class TestReseedSeriesMovesTheCounter(FrappeTestCase):
	"""The bug being locked out: reseed_series()'s update branch has never
	executed on this bench — every counter here is already ahead because of the
	site's own pre-migration history, so the e2e assertion above passes without
	the function ever writing anything. First execution would otherwise be on
	production.

	Driven against a throwaway key so nothing real is moved.
	"""

	KEY = "UPIRR-TEST-SERIES-"
	SHARED = ""

	def tearDown(self):
		frappe.db.delete("Series", {"name": self.KEY})
		frappe.db.commit()

	def _current(self, key):
		return frappe.db.get_value("Series", key, "current", order_by="name")

	def _set(self, key, value):
		from frappe.model.naming import NamingSeries

		NamingSeries(key).update_counter(value)
		frappe.db.commit()

	def test_a_counter_behind_the_migrated_data_is_advanced(self):
		self._set(self.KEY, 5)
		report = pull.reseed_series({self.KEY: 1473})
		self.assertEqual(int(self._current(self.KEY)), 1473)
		self.assertEqual(report[self.KEY]["action"], "reseeded")
		self.assertEqual(report[self.KEY]["was"], 5)

	def test_a_missing_counter_is_created_at_the_migrated_maximum(self):
		frappe.db.delete("Series", {"name": self.KEY})
		frappe.db.commit()
		pull.reseed_series({self.KEY: 1473})
		self.assertEqual(int(self._current(self.KEY)), 1473)

	def test_a_counter_already_ahead_is_never_moved_backwards(self):
		self._set(self.KEY, 9000)
		report = pull.reseed_series({self.KEY: 1473})
		self.assertEqual(int(self._current(self.KEY)), 9000)
		self.assertEqual(report[self.KEY]["action"], "already ahead")


class TestTheSharedCounterIsNeverWritten(FrappeTestCase):
	"""The bug being locked out: load_all accumulated the `''` counter key from
	Reservoir Pumping Record's `format:` autoname and reseeded it like any other.
	That key is shared by every format-named DocType on the site (137 of them on
	this bench, at 1,999,007) — writing it would renumber 137 unrelated doctypes
	on a live ERP. It is also unnecessary: RPR names embed the month, so a
	counter restarting at 1 cannot collide with a migrated `RPR-2026-04-` name.
	"""

	def setUp(self):
		self.before = frappe.db.get_value("Series", "", "current", order_by="name")

	def tearDown(self):
		"""Restore it if the guard failed and the counter actually moved."""
		from frappe.model.naming import NamingSeries

		if frappe.db.get_value("Series", "", "current", order_by="name") != self.before:
			NamingSeries("").update_counter(self.before)
		frappe.db.commit()

	def test_the_empty_key_is_declared_shared(self):
		self.assertIn("", pull.SHARED_COUNTER_KEYS)

	def test_a_value_far_above_the_current_counter_still_does_not_move_it(self):
		"""Deliberately asks for a raise, not a lower: only-raise would not save us."""
		wanted = int(self.before) + 10_000_000
		report = pull.reseed_series({"": wanted})
		self.assertEqual(
			frappe.db.get_value("Series", "", "current", order_by="name"), self.before
		)
		self.assertTrue(report[""]["action"].startswith("skipped"), report[""])

	def test_load_all_would_never_reseed_it_even_though_it_is_derived(self):
		"""series_targets still DERIVES the key -- the exclusion is the guard."""
		records = [{"name": "RPR-2026-04-357190"}]
		self.assertEqual(
			pull.series_targets("Reservoir Pumping Record", records), {"": 357190}
		)
