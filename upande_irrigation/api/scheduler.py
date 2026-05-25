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

import frappe

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_DAY_INDEX = {n: i for i, n in enumerate(_DAY_NAMES)}

_UPCOMING_PER_SECTION = 3


@frappe.whitelist()
def run(triggered_by="Manual"):
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
	today      = frappe.utils.getdate(frappe.utils.nowdate())
	weekday    = today.weekday()
	week_start = cfg.week_starts_on or "Thursday"
	start_idx  = _DAY_INDEX.get(week_start, 3)

	days_since_start = (weekday - start_idx) % 7
	this_week_start  = frappe.utils.add_days(today, -days_since_start)

	if (cfg.plan_for or "Current Week") == "Next Week":
		schedule_from = frappe.utils.add_days(this_week_start, 7)
	else:
		schedule_from = this_week_start

	schedule_to = frappe.utils.add_days(schedule_from, 6)

	log("=" * 60)
	log("Irrigation Scheduler Run")
	log("=" * 60)
	log(f"Triggered by:  {triggered_by}")
	log(f"Today:         {today} ({_DAY_NAMES[weekday]})")
	log(f"Week starts:   {week_start}")
	log(f"Plan for:      {cfg.plan_for or 'Current Week'}")
	log(f"Window:        {schedule_from} → {schedule_to}")
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

			cursor_dt = frappe.utils.get_datetime(f"{schedule_from} 00:00:00")

			for s in shifts:
				if aborted:
					break

				total_attempted += 1
				block_name = s["name"]

				# Skip if planner exists
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

				# Try insert
				try:
					planner = frappe.new_doc("Irrigation Planner")
					planner.farm            = farm
					planner.block           = block_name
					planner.from_date       = str(schedule_from)
					planner.to_date         = str(schedule_to)
					planner.scheduled_start = frappe.utils.get_datetime_str(cursor_dt)

					planner.insert(ignore_permissions=True)
					frappe.db.commit()

					sh = float(planner.shift_hours or 0)
					total_created += 1
					log(f"    OK    {block_name}  ({round(sh, 2)} hr)")

					shift_results.append({
						"farm": farm, "section": prefix, "block": block_name,
						"status": "Created", "planner": planner.name,
						"shift_hours": sh, "message": "",
					})

					if sh > 0:
						cursor_dt = frappe.utils.add_to_date(cursor_dt, hours=sh)

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
			p.name            AS planner,
			p.farm            AS farm,
			p.block           AS shift,
			p.scheduled_start AS started_at,
			p.scheduled_end   AS ends_at,
			p.shift_hours     AS shift_hours
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
			p.name            AS planner,
			p.farm            AS farm,
			p.block           AS shift,
			p.scheduled_start AS starts_at,
			p.scheduled_end   AS ends_at,
			p.shift_hours     AS shift_hours
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
		sec["current"] = {
			"planner":           row["planner"],
			"shift":             row["shift"],
			"started_at":        str(started_at),
			"ends_at":           str(ends_at),
			"shift_hours":       float(row["shift_hours"] or 0),
			"seconds_remaining": seconds_remaining,
			"pct_complete":      pct,
		}

	for row in upcoming:
		sec = ensure_section(row["farm"], _section_of(row["shift"]))
		if len(sec["upcoming"]) >= _UPCOMING_PER_SECTION:
			continue
		sec["upcoming"].append({
			"planner":     row["planner"],
			"shift":       row["shift"],
			"starts_at":   str(row["starts_at"]),
			"ends_at":     str(row["ends_at"]),
			"shift_hours": float(row["shift_hours"] or 0),
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
