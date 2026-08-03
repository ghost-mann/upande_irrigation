"""Irrigation Scheduler — Run API.

Reads config from Irrigation Scheduler (single). Creates an Irrigation
Scheduler Run audit record. Generates Irrigation Planners for the planning
window. Each planner insert is wrapped so a single failure doesn't abort
the rest. Returns a dict the UI can render.

Was previously the `Irrigation Scheduler Run` Server Script.
Callable as: frappe.call({method: 'upande_irrigation.api.scheduler.run'})

Also exposes `live_sections` — a read-only view of which section/shift is
being irrigated right now, with the next few upcoming shifts queued behind
it. Powers the /irrigation-now operator dashboard.
"""

from datetime import timedelta

import frappe

from upande_irrigation.engine import allocate as AL
from upande_irrigation.engine import demand as D
from upande_irrigation.events.irrigation_planner import (
	CHRONIC_DEFICIT_THRESHOLD_MM,
	CHRONIC_WEEKS_THRESHOLD,
	persistent_weeks,
	readings_for,
	settings_dict,
)

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_DAY_INDEX = {n: i for i, n in enumerate(_DAY_NAMES)}

_UPCOMING_PER_SECTION = 3

# Sections with no Irrigation Pump Profile share this bucket. They are sequenced
# together because an unknown pump might be one pump; assuming otherwise would
# schedule them concurrently on a main that may not carry it.
_UNKNOWN_PUMP = None


def plan_window(cfg, for_week=None):
	"""The week being planned.

	Normally derived from the configuration and today's date. `for_week` overrides
	it with an explicit start date — that is how historical weeks are regenerated,
	and it is the only way to plan a week that is not the current or next one.
	"""
	if for_week:
		week_start = frappe.utils.getdate(for_week)
		return week_start, frappe.utils.add_days(week_start, 6)

	today = frappe.utils.getdate(frappe.utils.nowdate())
	start_idx = _DAY_INDEX.get(cfg.week_starts_on or "Thursday", 3)
	week_start = frappe.utils.add_days(today, -((today.weekday() - start_idx) % 7))
	if (cfg.plan_for or "Current Week") == "Next Week":
		week_start = frappe.utils.add_days(week_start, 7)
	return week_start, frappe.utils.add_days(week_start, 6)


def pump_groups(farm):
	"""Section prefixes grouped by the pump that serves them.

	Sections sharing a pump_name must be sequenced against one capacity. The old
	engine capped each section against its own profile and then anchored every
	section at hour 0, so two sections on one pump each believed they had the whole
	week to themselves.
	"""
	rows = frappe.db.sql(
		"""
		SELECT pp.pump_name AS pump_name, w.warehouse_name AS section
		FROM `tabIrrigation Pump Profile` pp
		INNER JOIN `tabWarehouse` w ON w.name = pp.irrigation_section
		WHERE pp.farm = %s
		""",
		(farm,),
		as_dict=True,
	)
	groups = {}
	for r in rows:
		prefix = (r["section"] or "").replace("_SECTION", "").strip()
		if prefix:
			groups.setdefault(r["pump_name"] or "unnamed", []).append(prefix)
	return groups


def profile_for(farm, pump_name):
	if not pump_name:
		return None
	rows = frappe.get_all(
		"Irrigation Pump Profile",
		filters={"farm": farm, "pump_name": pump_name},
		fields=["water_target_m3_per_week", "pump_flow_rate_m3_per_hr"],
		limit=1,
	)
	return rows[0] if rows else None


def _by_pump(farm, created):
	"""Split a farm's shifts into (pump_name, members) pairs.

	Shifts whose section has no profile land under _UNKNOWN_PUMP together, so they
	are still sequenced rather than all anchored at hour 0.
	"""
	groups = pump_groups(farm)
	owner = {}
	for pump_name, prefixes in groups.items():
		for p in prefixes:
			owner[p] = pump_name

	buckets = {}
	for c in created:
		buckets.setdefault(owner.get(c["prefix"], _UNKNOWN_PUMP), []).append(c)

	# Named pumps first, unknown last, so the log reads predictably.
	return sorted(buckets.items(), key=lambda kv: (kv[0] is _UNKNOWN_PUMP, kv[0] or ""))


def _write_allocation(item, farm, capacity, verified, pump_label, settings, result_of, log):
	"""Persist one shift's granted hours, windows and warnings."""
	windows = item["windows"]
	granted = item["placed_hours"] if windows else 0.0
	mm_hr = settings["default_application_rate_mm_hr"]
	coverage = settings["default_irrigation_coverage"]
	delivered = granted * mm_hr * (coverage / 100.0)
	total = float(item.get("total_needed_mm") or 0)
	unmet = max(0.0, total - delivered)

	warnings = []
	if not verified:
		warnings.append(
			f"Pump capacity unverified: no Irrigation Pump Profile for {pump_label}; "
			f"assumed {capacity:.0f} hr/wk. Hours below are demand, not a capped figure."
		)
	elif item["capped"]:
		warnings.append(
			f"Pump capacity capped ({pump_label}, {capacity:.1f} hr/wk): needed "
			f"{item['required_hours']:.2f} hr, granted {granted:.2f} hr."
		)
	if item["required_hours"] > 0 and not windows:
		warnings.append(
			f"Not scheduled: the {pump_label} queue filled the week before this shift's "
			f"turn. {item['required_hours']:.2f} hr carries forward."
		)
	if unmet > CHRONIC_DEFICIT_THRESHOLD_MM:
		warnings.append(
			f"CHRONIC DEFICIT: {unmet:.1f} mm unmet — exceeds "
			f"{CHRONIC_DEFICIT_THRESHOLD_MM} mm threshold. Trees may be stressed."
		)

	block, plan_from = frappe.db.get_value(
		"Irrigation Planner", item["key"], ["block", "from_date"]
	)
	streak = persistent_weeks(farm, block, plan_from)
	if streak >= CHRONIC_WEEKS_THRESHOLD:
		warnings.append(
			f"PERSISTENT DEFICIT: this shift has carried deficit forward for {streak} "
			"consecutive weeks. Review pump capacity, coverage, or schedule."
		)

	frappe.db.set_value(
		"Irrigation Planner",
		item["key"],
		{
			"shift_hours":        round(granted, 2),
			"cycles_count":       item["cycles_count"],
			"cycle_hours_each":   round(item["cycle_hours_each"], 2),
			"cycle_plan":         (
				f"{item['cycles_count']} × {item['cycle_hours_each']:.2f} hr"
				if item["cycles_count"] else ""
			),
			"scheduled_start":    frappe.utils.get_datetime_str(windows[0][0]) if windows else None,
			"scheduled_end":      frappe.utils.get_datetime_str(windows[-1][1]) if windows else None,
			"delivered_depth_mm": round(delivered, 2),
			"unmet_deficit_mm":   round(unmet, 2),
			"capacity_warning":   " | ".join(warnings),
		},
		update_modified=False,
	)

	row = result_of.get(item["key"])
	if row is not None:
		row["shift_hours"] = round(granted, 2)
		row["message"] = warnings[0] if warnings else ""

	log(f"    {block}: {round(granted, 2)} hr in {item['cycles_count']} cycle(s), "
	    f"{round(unmet, 1)} mm unmet")


@frappe.whitelist()
def run(triggered_by="Manual", for_week=None):
	if triggered_by not in ("Cron", "Manual", "API"):
		triggered_by = "Manual"

	log_lines = []
	error_lines = []
	shift_results = []

	def log(msg):
		line = str(msg)
		print(line)
		log_lines.append(line)

	def log_err(msg):
		line = str(msg)
		print("ERROR: " + line)
		log_lines.append("ERROR: " + line)
		error_lines.append(line)

	# ── Load config ─────────────────────────────────────────────
	cfg = frappe.get_doc("Irrigation Scheduler")

	if not cfg.enabled:
		frappe.response["message"] = {
			"ok": False,
			"reason": "Scheduler is disabled. Enable it in Irrigation Scheduler.",
		}
		raise frappe.ValidationError("Scheduler is disabled.")

	if triggered_by == "Cron" and not cfg.auto_run_enabled:
		log("Cron tried to run but auto_run_enabled = 0. Exiting silently.")
		frappe.response["message"] = {
			"ok": False,
			"reason": "Auto-run on cron is disabled.",
		}
		raise frappe.ValidationError("Auto-run disabled.")

	# ── Determine planning window ───────────────────────────────
	today = frappe.utils.getdate(frappe.utils.nowdate())
	schedule_from, schedule_to = plan_window(cfg, for_week)
	measured_from, measured_to = D.measured_window(schedule_from)
	settings = settings_dict()

	log("=" * 60)
	log("Irrigation Scheduler Run")
	log("=" * 60)
	log(f"Triggered by:  {triggered_by}")
	log(f"Today:         {today} ({_DAY_NAMES[today.weekday()]})")
	log(f"Week starts:   {cfg.week_starts_on or 'Thursday'}")
	log(f"Plan for:      {cfg.plan_for or 'Current Week'}")
	log(f"Planning:      {schedule_from} → {schedule_to}")
	log(f"Measured over: {measured_from} → {measured_to}  (elapsed)")
	log(f"Min readings:  {settings['min_weather_days']} of 7 days")
	log("")

	# ── Create run log record (status = Running) ────────────────
	run_doc = frappe.new_doc("Irrigation Scheduler Run")
	run_doc.run_started_at    = frappe.utils.now_datetime()
	run_doc.status            = "Running"
	run_doc.triggered_by      = triggered_by
	run_doc.triggered_by_user = frappe.session.user if triggered_by != "Cron" else None
	run_doc.from_date         = schedule_from
	run_doc.to_date           = schedule_to
	run_doc.insert(ignore_permissions=True)
	run_name = run_doc.name
	frappe.db.commit()  # keep a Running record visible on mid-run crash

	log(f"Run record: {run_name}")
	log("")

	# ── Resolve farms ───────────────────────────────────────────
	farms_in_table = []
	if cfg.get("farms_to_process"):
		farms_in_table = [r.farm for r in cfg.farms_to_process if r.farm and r.enabled]

	if farms_in_table:
		farms = farms_in_table
		log(f"Farms (from config table): {', '.join(farms)}")
	else:
		farm_rows = frappe.get_all(
			"Farm",
			filters={"is_irrigation_farm": 1},
			fields=["name"],
			order_by="name asc",
		)
		farms = [f["name"] for f in farm_rows]
		if farms:
			log(f"Farms (is_irrigation_farm=1): {', '.join(farms)}")
		else:
			log("No farms found with is_irrigation_farm=1")

	log("")

	# ── Counters ────────────────────────────────────────────────
	total_created = total_skipped = total_failed = total_attempted = 0
	sections_touched = set()
	max_errors = int(cfg.max_errors_before_abort or 10)
	aborted = False

	# ── Main loop ───────────────────────────────────────────────
	for farm in farms:
		if aborted:
			break

		log("─" * 60)
		log(f"FARM: {farm}")
		log("─" * 60)

		# ── Refuse before creating anything ─────────────────────
		# The measured week is shared by every shift on the farm, so this is one
		# check, not one per shift. A farm-week we cannot measure produces no
		# planners at all — the old engine created them anyway and every one said
		# "No irrigation required".
		farm_agg = D.aggregate(readings_for(farm, measured_from, measured_to))
		plannable, why = D.is_plannable(farm_agg, settings)
		if not plannable:
			log_err(f"  {why} ({measured_from} → {measured_to}). No planners created.")
			shift_results.append({
				"farm": farm, "section": "—", "block": "—",
				"status": "Skipped", "planner": None, "shift_hours": 0,
				"message": why,
			})
			continue

		log(f"  Measured week: {farm_agg['days']} days, "
		    f"{farm_agg['rainfall_mm']} mm rain, {farm_agg['et_crop_mm']} mm ET crop")

		section_warehouses = frappe.db.sql("""
			SELECT name, warehouse_name
			FROM `tabWarehouse`
			WHERE custom_farm = %s
			  AND warehouse_type = 'Section'
			  AND COALESCE(disabled, 0) = 0
			ORDER BY warehouse_name
		""", (farm,), as_dict=True)

		if not section_warehouses:
			log("  No sections found. Skipping.")
			continue

		log("  Sections: " + ", ".join(sw["warehouse_name"] for sw in section_warehouses))
		log("")

		# ── Pass 1: insert every shift on the farm, capture demand ──
		# Farm-level, not section-level: allocation groups by pump, and one pump can
		# serve several sections.
		created = []
		result_of = {}

		for sw in section_warehouses:
			if aborted:
				break

			prefix = (sw["warehouse_name"] or "").replace("_SECTION", "").strip()
			if not prefix:
				log_err(f"  Could not derive prefix from {sw['warehouse_name']}")
				continue

			shifts = frappe.db.sql("""
				SELECT name
				FROM `tabBlock Type`
				WHERE name LIKE %s
				  AND is_active = 1
				  AND farm = %s
				ORDER BY CAST(SUBSTRING_INDEX(name, ' - SHIFT ', -1) AS UNSIGNED) ASC
			""", (prefix + " - SHIFT %", farm), as_dict=True)

			if not shifts:
				log(f"  [{prefix}] no active shifts")
				continue

			log(f"  [{prefix}] {len(shifts)} active shifts")
			sections_touched.add(prefix)

			for s in shifts:
				if aborted:
					break

				total_attempted += 1
				block_name = s["name"]

				if cfg.skip_existing:
					existing = frappe.db.exists("Irrigation Planner", {
						"farm":      farm,
						"block":     block_name,
						"from_date": str(schedule_from),
						"to_date":   str(schedule_to),
						"docstatus": ["<", 2],
					})
					if existing:
						total_skipped += 1
						log(f"    SKIP  {block_name}  (exists: {existing})")
						shift_results.append({
							"farm": farm, "section": prefix, "block": block_name,
							"status": "Skipped", "planner": existing,
							"shift_hours": 0,
							"message": "Planner already exists for this week",
						})
						continue

				try:
					planner = frappe.new_doc("Irrigation Planner")
					planner.farm      = farm
					planner.block     = block_name
					planner.from_date = str(schedule_from)
					planner.to_date   = str(schedule_to)

					planner.insert(ignore_permissions=True)
					frappe.db.commit()

					required = float(planner.required_hours or 0)
					total_created += 1
					log(f"    OK    {block_name}  needs {round(required, 2)} hr")

					created.append({
						"key":             planner.name,
						"block":           block_name,
						"prefix":          prefix,
						"required_hours":  required,
						"total_needed_mm": float(planner.total_water_needed_mm or 0),
					})
					row = {
						"farm": farm, "section": prefix, "block": block_name,
						"status": "Created", "planner": planner.name,
						"shift_hours": 0, "message": "",
					}
					result_of[planner.name] = row
					shift_results.append(row)

				except Exception as e:
					frappe.db.rollback()
					total_failed += 1
					err_msg = str(e)[:500]
					log_err(f"  {block_name} → {err_msg}")

					shift_results.append({
						"farm": farm, "section": prefix, "block": block_name,
						"status": "Error", "planner": None,
						"shift_hours": 0, "message": err_msg,
					})

					if total_failed >= max_errors:
						log("")
						log(f"!! ABORTED: {total_failed} errors reached "
						    f"max_errors_before_abort ({max_errors})")
						aborted = True
						break

		# ── Pass 2: allocate each pump's capacity across its shifts ──
		if created:
			log("")
			for pump_name, members in _by_pump(farm, created):
				capacity, verified = AL.pump_capacity_hours(profile_for(farm, pump_name))
				pump_label = pump_name or "unknown pump"
				needed = sum(m["required_hours"] for m in members)
				log(f"  PUMP {pump_label}: {len(members)} shifts need "
				    f"{round(needed, 2)} hr against {capacity} hr/wk"
				    f"{'' if verified else ' (unverified)'}")

				laid = AL.lay_out(AL.grant(members, capacity), schedule_from, settings)
				for item in laid:
					_write_allocation(
						item, farm, capacity, verified, pump_label, settings, result_of, log
					)
				frappe.db.commit()

		log("")

	# ── Final status ────────────────────────────────────────────
	if aborted:
		final_status = "Aborted"
	elif total_failed > 0 and total_created > 0:
		final_status = "Partial"
	elif total_failed > 0 and total_created == 0:
		final_status = "Failed"
	else:
		final_status = "Success"

	summary = (
		f"{total_created} created, {total_skipped} skipped, "
		f"{total_failed} failed across {len(sections_touched)} sections, "
		f"{len(farms)} farm(s)"
	)

	log("")
	log("=" * 60)
	log(f"DONE — {final_status}")
	log("=" * 60)
	log(summary)

	# ── Finalize run record ─────────────────────────────────────
	run_doc = frappe.get_doc("Irrigation Scheduler Run", run_name)
	for r in shift_results:
		run_doc.append("shift_results", r)

	run_doc.run_completed_at   = frappe.utils.now_datetime()
	run_doc.duration_seconds   = frappe.utils.time_diff_in_seconds(
		run_doc.run_completed_at, run_doc.run_started_at
	)
	run_doc.status             = final_status
	run_doc.farms_processed    = len(farms)
	run_doc.sections_processed = len(sections_touched)
	run_doc.shifts_attempted   = total_attempted
	run_doc.planners_created   = total_created
	run_doc.planners_skipped   = total_skipped
	run_doc.planners_failed    = total_failed
	run_doc.summary            = summary
	run_doc.full_log           = "\n".join(log_lines)
	run_doc.error_summary      = "\n".join(error_lines) if error_lines else ""
	run_doc.save(ignore_permissions=True)

	# Update parent config
	cfg = frappe.get_doc("Irrigation Scheduler")
	cfg.last_run_at      = run_doc.run_completed_at
	cfg.last_run_status  = final_status
	cfg.last_run_summary = summary
	cfg.total_runs       = int(cfg.total_runs or 0) + 1
	cfg.save(ignore_permissions=True)

	frappe.db.commit()

	return {
		"ok":      final_status in ("Success", "Partial"),
		"run":     run_doc.name,
		"status":  final_status,
		"summary": summary,
		"created": total_created,
		"skipped": total_skipped,
		"failed":  total_failed,
	}


def backfill(from_week, to_week, triggered_by="API"):
	"""Regenerate planners week by week across a historical range.

	Deliberately not whitelisted: it can create thousands of records. Run it from
	bench. Each week gets its own Irrigation Scheduler Run, so a farm-week that
	refuses for want of weather is recorded rather than silently absent.

	    bench --site <site> execute upande_irrigation.api.scheduler.backfill \\
	        --kwargs "{'from_week':'2024-12-05','to_week':'2026-07-16'}"
	"""
	week = frappe.utils.getdate(from_week)
	last = frappe.utils.getdate(to_week)
	summary = []

	while week <= last:
		try:
			out = run(triggered_by=triggered_by, for_week=str(week))
			summary.append({"week": str(week), **{k: out[k] for k in ("status", "created", "skipped", "failed")}})
		except Exception as e:
			frappe.db.rollback()
			summary.append({"week": str(week), "status": "Error", "message": str(e)[:200]})
		week = frappe.utils.add_days(week, 7)

	created = sum(s.get("created") or 0 for s in summary)
	print(f"\nBackfill {from_week} → {to_week}: {len(summary)} weeks, {created} planners created")
	for s in summary:
		print(f"  {s['week']}  {s.get('status')}  created={s.get('created', 0)} skipped={s.get('skipped', 0)}")
	return {"weeks": summary, "created": created}


def _section_of(shift_name):
	if shift_name and " - SHIFT " in shift_name:
		return shift_name.split(" - SHIFT ")[0]
	return shift_name or "?"


@frappe.whitelist()
def live_sections(farm=None):
	"""Per-section live view: which shift is irrigating now + the queue.

	Returns:
	  {
	    farms: [{
	      farm,
	      sections: [{
	        section,
	        current: {planner, shift, started_at, ends_at, shift_hours,
	                  seconds_remaining, pct_complete} | null,
	        upcoming: [{planner, shift, starts_at, ends_at, shift_hours}, ...]
	      }]
	    }],
	    generated_at, now, farm_filter
	  }
	"""
	now_dt = frappe.utils.now_datetime()
	now_str = frappe.utils.get_datetime_str(now_dt)

	args = {"now": now_str}
	farm_clause = ""
	if farm:
		farm_clause = " AND p.farm = %(farm)s"
		args["farm"] = farm

	active = frappe.db.sql(f"""
		SELECT
			p.name             AS planner,
			p.farm             AS farm,
			p.block            AS shift,
			p.scheduled_start  AS started_at,
			p.scheduled_end    AS ends_at,
			p.shift_hours      AS shift_hours,
			p.cycles_count     AS cycles_count,
			p.cycle_hours_each AS cycle_hours_each
		FROM `tabIrrigation Planner` p
		WHERE p.docstatus < 2
		  AND p.scheduled_start IS NOT NULL
		  AND p.scheduled_end IS NOT NULL
		  AND %(now)s BETWEEN p.scheduled_start AND p.scheduled_end
		  {farm_clause}
		ORDER BY p.farm, p.block
	""", args, as_dict=True)

	upcoming = frappe.db.sql(f"""
		SELECT
			p.name             AS planner,
			p.farm             AS farm,
			p.block            AS shift,
			p.scheduled_start  AS starts_at,
			p.scheduled_end    AS ends_at,
			p.shift_hours      AS shift_hours,
			p.cycles_count     AS cycles_count,
			p.cycle_hours_each AS cycle_hours_each
		FROM `tabIrrigation Planner` p
		WHERE p.docstatus < 2
		  AND p.scheduled_start IS NOT NULL
		  AND p.scheduled_start > %(now)s
		  {farm_clause}
		ORDER BY p.scheduled_start ASC
	""", args, as_dict=True)

	farms_map = {}

	def ensure_section(fname, sec):
		fmap = farms_map.setdefault(fname, {})
		if sec not in fmap:
			fmap[sec] = {"section": sec, "current": None, "upcoming": []}
		return fmap[sec]

	for row in active:
		sec = ensure_section(row["farm"], _section_of(row["shift"]))
		started_at = row["started_at"]
		ends_at = row["ends_at"]
		seconds_remaining = max(0, int((ends_at - now_dt).total_seconds()))
		total_seconds = max(1, int((ends_at - started_at).total_seconds()))
		elapsed = total_seconds - seconds_remaining
		pct = round(100.0 * elapsed / total_seconds, 1)
		cycles_count = int(row["cycles_count"] or 0)
		cycle_hours_each = float(row["cycle_hours_each"] or 0)
		current_cycle = 0
		cycles = []
		if cycles_count and cycle_hours_each > 0:
			elapsed_hours = elapsed / 3600.0
			current_cycle = min(cycles_count, int(elapsed_hours // cycle_hours_each) + 1)
			cyc_seconds = cycle_hours_each * 3600.0
			for k in range(cycles_count):
				c_start = started_at + timedelta(seconds=k * cyc_seconds)
				c_end   = started_at + timedelta(seconds=(k + 1) * cyc_seconds)
				if now_dt >= c_end:
					state = "done"
				elif now_dt < c_start:
					state = "upcoming"
				else:
					state = "running"
				cycles.append({
					"n":         k + 1,
					"starts_at": str(c_start),
					"ends_at":   str(c_end),
					"hours":     round(cycle_hours_each, 2),
					"state":     state,
				})
		sec["current"] = {
			"planner":           row["planner"],
			"shift":             row["shift"],
			"started_at":        str(started_at),
			"ends_at":           str(ends_at),
			"shift_hours":       float(row["shift_hours"] or 0),
			"seconds_remaining": seconds_remaining,
			"pct_complete":      pct,
			"cycles_count":      cycles_count,
			"cycle_hours_each":  cycle_hours_each,
			"current_cycle":     current_cycle,
			"cycles":            cycles,
		}

	for row in upcoming:
		sec = ensure_section(row["farm"], _section_of(row["shift"]))
		if len(sec["upcoming"]) >= _UPCOMING_PER_SECTION:
			continue
		sec["upcoming"].append({
			"planner":          row["planner"],
			"shift":            row["shift"],
			"starts_at":        str(row["starts_at"]),
			"ends_at":          str(row["ends_at"]),
			"shift_hours":      float(row["shift_hours"] or 0),
			"cycles_count":     int(row["cycles_count"] or 0),
			"cycle_hours_each": float(row["cycle_hours_each"] or 0),
		})

	farms_out = []
	for fname in sorted(farms_map.keys()):
		sections = farms_map[fname]
		farms_out.append({
			"farm":     fname,
			"sections": [sections[k] for k in sorted(sections.keys())],
		})

	return {
		"farms":        farms_out,
		"generated_at": now_str,
		"now":          now_str,
		"farm_filter":  farm or "all",
	}
