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

	def test_loading_the_singles_never_touches_the_shift_mapping(self):
		"""shift_blocks holds the 86 mapping rows built by the shift loader; the
		source's own copy is the pre-migration one and must not overwrite it."""
		preserve = dict((dt, p) for dt, _f, p in pull.SINGLES)
		self.assertIn("shift_blocks", preserve["Irrigation Scheduler"])


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
