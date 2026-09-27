"""Irrigation Control — valve state + geometry API.

For each Valve in `Tank And Valve` (asset_type='Valve'):

  schedule_state   ON if NOW is inside a Planned/Running cycle on the Irrigation
                   Run Sheet for a shift containing the valve's block. OFF otherwise.

  manual_state     Operator override: Auto / Forced Open / Forced Closed.

  effective_state  manual override wins; else schedule_state.

Endpoints
─────────
GET  /api/method/upande_irrigation.api.valves.list_states
        → {valves: [{name, asset_label, farm, block, tank, schedule_state,
                     manual_state, effective_state, schedule_planner,
                     schedule_window_end, ...}], generated_at}

POST /api/method/upande_irrigation.api.valves.set_override
        args: {valve, state}  where state in {Auto, Forced Open, Forced Closed}
        → {ok, valve, manual_state, by, at}

POST /api/method/upande_irrigation.api.valves.set_override_bulk
        args: {state, valves?, section?, farm?, all_valves?}
        → {ok, state, updated: [names], count}

GET  /api/method/upande_irrigation.api.valves.blocks_geojson
        args: {farm?}
        → FeatureCollection of block boundaries (Warehouse.custom_raw_geojson)

GET  /api/method/upande_irrigation.api.valves.geojson
        → FeatureCollection of every Tank And Valve row that has a
          location_geojson set. Drop-in replacement for the
          upande_scp.serverscripts.get_tanks_valves.get_tanks_valves_geojson
          endpoint the dashboard used to call.
"""

import json

import frappe


_VALID_OVERRIDES = ("Auto", "Forced Open", "Forced Closed")


@frappe.whitelist()
def list_states(farm=None):
	"""Return live valve states. Optionally filter by farm."""
	now_dt = frappe.utils.now_datetime()
	now_str = frappe.utils.get_datetime_str(now_dt)

	# ── Pull all valves ──────────────────────────────────────────
	filters = {"asset_type": "Valve"}
	if farm:
		filters["farm"] = farm
	valves = frappe.get_all(
		"Tank And Valve",
		filters=filters,
		fields=[
			"name", "asset_label", "farm", "block", "tank",
			"manual_state", "manual_state_set_at", "manual_state_set_by",
			"location_geojson",
		],
		order_by="block asc, asset_label asc",
		limit_page_length=0,
	)

	if not valves:
		return {"valves": [], "generated_at": now_str, "now": now_str}

	# ── Build the lookup: valve.block → the run-sheet cycle running now ──
	# A valve's `block` belongs to shifts via `Irrigation Shift Block` (the child
	# table of the Irrigation Scheduler single). A cycle on today's Irrigation Run
	# Sheet that is Planned or Running and whose window contains NOW turns every
	# valve of that shift's blocks ON. Replaces the retired weekly planner windows.
	cycles = frappe.db.sql(
		"""
		SELECT sb.block AS sub_block, r.parent AS planner, r.shift AS shift,
		       r.planned_start AS start_dt, r.planned_end AS end_dt, r.planned_hours AS shift_hours,
		       r.status AS status
		FROM `tabIrrigation Run` r
		INNER JOIN `tabIrrigation Run Sheet` rs ON rs.name = r.parent
		INNER JOIN `tabIrrigation Shift Block` sb
		        ON sb.shift = r.shift AND sb.parenttype = 'Irrigation Scheduler' AND sb.parentfield = 'shift_blocks'
		WHERE r.parenttype = 'Irrigation Run Sheet'
		  AND r.status IN ('Planned', 'Running')
		  AND r.planned_start IS NOT NULL
		  AND rs.date >= DATE_SUB(DATE(%(now)s), INTERVAL 1 DAY)
		  -- A cycle left "Running" keeps its valves on only until 6 h past its
		  -- planned end; after that it is a forgotten tick, not a running valve.
		  AND (r.planned_start > %(now)s OR r.planned_end >= %(now)s
		       OR (r.status = 'Running' AND r.planned_end >= DATE_SUB(%(now)s, INTERVAL 6 HOUR)))
		ORDER BY r.planned_start ASC
		""",
		{"now": now_str},
		as_dict=True,
	)
	now_dt = frappe.utils.get_datetime(now_str)
	active_by_subblock = {}
	next_by_subblock = {}
	for c in cycles:
		start, end = frappe.utils.get_datetime(c["start_dt"]), frappe.utils.get_datetime(c["end_dt"])
		if c["status"] == "Running" or start <= now_dt <= end:
			active_by_subblock.setdefault(c["sub_block"], c)
		elif start > now_dt:
			next_by_subblock.setdefault(c["sub_block"], c["start_dt"])

	# ── Stitch state per valve ───────────────────────────────────
	out = []
	for v in valves:
		active_row = active_by_subblock.get(v["block"])
		schedule_state = "ON" if active_row else "OFF"

		manual = v.get("manual_state") or "Auto"
		if manual == "Forced Open":
			effective = "ON"
		elif manual == "Forced Closed":
			effective = "OFF"
		else:
			effective = schedule_state

		out.append({
			"name":                v["name"],
			"asset_label":         v["asset_label"],
			"farm":                v["farm"],
			"block":               v["block"],
			"tank":                v["tank"],
			"schedule_state":      schedule_state,
			"manual_state":        manual,
			"effective_state":     effective,
			"override_active":     manual != "Auto",
			"override_set_at":     str(v.get("manual_state_set_at") or "") or None,
			"override_set_by":     v.get("manual_state_set_by"),
			"schedule_planner":    active_row["planner"]  if active_row else None,
			"schedule_shift":      active_row["shift"]    if active_row else None,
			"schedule_started_at": str(active_row["start_dt"]) if active_row else None,
			"schedule_ends_at":    str(active_row["end_dt"])   if active_row else None,
			"next_scheduled_at":   str(next_by_subblock.get(v["block"]) or "") or None,
			"location_geojson":    v.get("location_geojson"),
		})

	# Tank rollups for grouping in the UI
	tanks = frappe.get_all(
		"Tank And Valve",
		filters={"asset_type": "Tank"},
		fields=["name", "asset_label", "farm"],
		order_by="farm asc, asset_label asc",
	)

	return {
		"valves":       out,
		"tanks":        tanks,
		"generated_at": now_str,
		"now":          now_str,
		"farm_filter":  farm or "all",
	}


def log_event(doc, before, source="Operator"):
	"""Append a Valve Event for a manual-state change. The water balance reads these
	open intervals as the strongest record of what was actually irrigated."""
	if before == doc.manual_state or not frappe.db.exists("DocType", "Valve Event"):
		return
	frappe.get_doc({
		"doctype": "Valve Event", "valve": doc.name, "block": doc.block, "at": frappe.utils.now_datetime(),
		"from_state": before, "to_state": doc.manual_state, "source": source, "user": frappe.session.user,
	}).insert(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def set_override(valve, state):
	"""Operator override of a valve's manual state."""
	if state not in _VALID_OVERRIDES:
		frappe.throw(
			f"`state` must be one of {', '.join(_VALID_OVERRIDES)}. Got: {state!r}"
		)

	doc = frappe.get_doc("Tank And Valve", valve)
	if doc.asset_type != "Valve":
		frappe.throw(f"{valve} is not a Valve (asset_type={doc.asset_type}).")

	before = doc.manual_state or "Auto"
	doc.manual_state = state
	# manual_state_set_at/by are stamped in the validate() controller hook
	doc.save(ignore_permissions=False)
	log_event(doc, before)
	frappe.db.commit()

	return {
		"ok":           True,
		"valve":        doc.name,
		"manual_state": doc.manual_state,
		"set_by":       doc.manual_state_set_by,
		"set_at":       str(doc.manual_state_set_at or ""),
	}


@frappe.whitelist(methods=["POST"])
def set_override_bulk(state, valves=None, section=None, farm=None, all_valves=0):
	"""Apply one manual state to many valves at once.

	Scope is any combination of an explicit `valves` list (JSON or list), a
	`section` (valves whose block's parent_warehouse is that section) and a
	`farm`; they intersect. With no scope at all the call is refused unless
	`all_valves` is set — "no filter" must never silently mean "every valve on
	every farm".

	Each valve is saved through its controller (which stamps set_at/by) with the
	caller's permissions, and the whole batch commits once: a permission error
	part-way leaves no valve changed.
	"""
	if state not in _VALID_OVERRIDES:
		frappe.throw(f"`state` must be one of {', '.join(_VALID_OVERRIDES)}. Got: {state!r}")
	if not frappe.has_permission("Tank And Valve", "write"):
		raise frappe.PermissionError("Not permitted to override valves.")

	if isinstance(valves, str):
		valves = json.loads(valves) if valves.strip() else None
	if isinstance(valves, str):  # a lone JSON string: one valve, not its characters
		valves = [valves]
	if valves is not None and not isinstance(valves, list | tuple):
		frappe.throw("`valves` must be a list of valve names.")
	all_valves = frappe.utils.cint(all_valves)
	if not (valves or section or farm or all_valves):
		frappe.throw("Choose valves, a section or a farm — or pass all_valves=1 to override every valve.")

	filters = {"asset_type": "Valve"}
	if valves:
		filters["name"] = ["in", list(valves)]
	if farm:
		filters["farm"] = farm
	if section:
		blocks = frappe.get_all("Warehouse", filters={"parent_warehouse": section}, pluck="name")
		if not blocks:
			return {"ok": True, "state": state, "updated": [], "count": 0}
		filters["block"] = ["in", blocks]

	names = frappe.get_all("Tank And Valve", filters=filters, pluck="name", order_by="name asc")
	updated = []
	for name in names:
		doc = frappe.get_doc("Tank And Valve", name)
		if doc.manual_state == state:
			continue
		before = doc.manual_state or "Auto"
		doc.manual_state = state
		doc.save()
		log_event(doc, before)
		updated.append(name)
	frappe.db.commit()

	return {"ok": True, "state": state, "updated": updated, "count": len(updated)}


@frappe.whitelist()
def blocks_geojson(farm=None):
	"""Block boundaries for the Field Map, from Warehouse.custom_raw_geojson.

	That field is shipped by upande_scp, which not every irrigation site has.
	The map used to request it straight from the client, so on such a site the
	whole block query failed; here its absence is reported, not raised.
	"""
	empty = {"type": "FeatureCollection", "features": [], "meta": {"unavailable": True, "blocks": 0}}
	meta = frappe.get_meta("Warehouse")
	if not meta.has_field("custom_raw_geojson"):
		return empty

	# Without a "Block" warehouse type there is no way to tell blocks from any
	# other mapped warehouse (Kaitet has 52 greenhouses with geometry), and
	# without custom_farm the farm filter cannot apply — say so, don't guess.
	if not frappe.db.exists("Warehouse Type", "Block"):
		return {**empty, "meta": {**empty["meta"], "reason": "no Block warehouse type"}}
	if farm and not meta.has_field("custom_farm"):
		return {**empty, "meta": {**empty["meta"], "reason": "Warehouse has no custom_farm"}}
	filters = {"disabled": 0, "is_group": 0, "warehouse_type": "Block"}
	if farm:
		filters["custom_farm"] = farm
	fields = ["name", "warehouse_name", "parent_warehouse", "custom_raw_geojson"]
	if meta.has_field("custom_farm"):
		fields.append("custom_farm")

	features = []
	rows = frappe.get_all(
		"Warehouse", filters=filters,
		fields=fields, order_by="name asc", limit_page_length=0,
	)
	for wh in rows:
		raw = wh.get("custom_raw_geojson")
		if not raw:
			continue
		try:
			geo = json.loads(raw)
		except (ValueError, TypeError):
			continue
		parts = geo.get("features") if isinstance(geo, dict) and geo.get("type") == "FeatureCollection" else [geo]
		for f in parts or []:
			geometry = f.get("geometry") if isinstance(f, dict) and f.get("type") == "Feature" else f
			if not isinstance(geometry, dict) or not geometry.get("type"):
				continue
			features.append({
				"type": "Feature",
				"geometry": geometry,
				"properties": {
					"block": wh["name"],
					"block_label": wh.get("warehouse_name") or wh["name"],
					"farm": wh.get("custom_farm") or "",
					"section": wh.get("parent_warehouse") or "",
				},
			})
	return {
		"type": "FeatureCollection",
		"features": features,
		"meta": {"unavailable": False, "blocks": len(rows), "farm": farm},
	}


@frappe.whitelist(allow_guest=False)
def geojson(farm=None, asset_type=None):
	"""Return a FeatureCollection of every Tank And Valve row.

	Each stored `location_geojson` is itself a GeoJSON Feature with a Point
	(or any geometry) and empty properties. We keep the geometry verbatim
	and inject the row's identifying columns into `properties` so the map
	can label, group, and link.

	Rows with missing / unparseable geojson are skipped silently — the
	dashboard treats `features` length as the source-of-truth count.
	"""
	filters = {}
	if farm:
		filters["farm"] = farm
	if asset_type:
		filters["asset_type"] = asset_type  # 'Valve' or 'Tank'

	rows = frappe.get_all(
		"Tank And Valve",
		filters=filters,
		fields=[
			"name", "asset_type", "asset_label", "farm", "block", "tank",
			"height", "radius", "location_geojson",
		],
		order_by="asset_type asc, name asc",
		limit_page_length=0,
	)

	features = []
	skipped = 0
	for r in rows:
		raw = r.get("location_geojson")
		if not raw:
			skipped += 1
			continue
		try:
			feat = json.loads(raw)
		except (ValueError, TypeError):
			skipped += 1
			continue

		# Accept either a Feature wrapper or a bare geometry.
		if isinstance(feat, dict) and feat.get("type") == "Feature":
			geometry = feat.get("geometry")
		elif isinstance(feat, dict) and feat.get("type") in ("Point", "Polygon", "MultiPolygon", "LineString"):
			geometry = feat
		else:
			skipped += 1
			continue

		if not geometry:
			skipped += 1
			continue

		features.append({
			"type":     "Feature",
			"geometry": geometry,
			"properties": {
				"asset_name":  r["name"],
				"asset_label": r.get("asset_label"),
				"asset_type":  r.get("asset_type"),
				"farm":        r.get("farm"),
				"block":       r.get("block"),
				"tank":        r.get("tank"),
				"height":      r.get("height"),
				"radius":      r.get("radius"),
			},
		})

	return {
		"type": "FeatureCollection",
		"features": features,
		"meta": {
			"total":   len(rows),
			"emitted": len(features),
			"skipped": skipped,
			"filters": {"farm": farm, "asset_type": asset_type},
		},
	}


_PLANNER_FIELDS = [
	"name", "block", "docstatus", "from_date", "to_date", "required_hours", "shift_hours",
	"cycles_count", "cycle_hours_each", "cycle_plan", "scheduled_start", "scheduled_end",
	"this_week_deficit", "total_water_needed_mm", "z_risk_level", "no_irrigation_reason",
	"capacity_warning", "actually_irrigated",
]


@frappe.whitelist()
def block_info(block):
	"""Everything the Field Map's block card shows for one block warehouse:
	the block itself, the shifts it belongs to (Irrigation Shift Block), this
	week's planner for each shift (the one covering today, else the latest),
	its valves and its latest irrometer reading. Valve state is not repeated
	here — the map already holds it from list_states."""
	if not frappe.db.exists("Warehouse", block):
		raise frappe.DoesNotExistError(f"Block {block} not found")

	meta = frappe.get_meta("Warehouse")
	fields = ["name", "warehouse_name", "parent_warehouse", "warehouse_type", "is_group"]
	fields += [f for f in ("custom_farm", "custom_area_ha") if meta.has_field(f)]
	wh = frappe.db.get_value("Warehouse", block, fields, as_dict=True)

	shifts = frappe.get_all(
		"Irrigation Shift Block",
		filters={"parenttype": "Irrigation Scheduler", "parentfield": "shift_blocks", "block": block},
		fields=["shift", "farm", "is_active", "application_rate_mm_hr", "irrigation_coverage"],
		order_by="shift asc",
	)
	# Every block the same shifts water, so the card can say "watered with …".
	names = [s["shift"] for s in shifts]
	partners = {}
	if names:
		for r in frappe.get_all(
			"Irrigation Shift Block",
			filters={"parenttype": "Irrigation Scheduler", "parentfield": "shift_blocks", "shift": ["in", names]},
			fields=["shift", "block"],
		):
			if r["block"] != block:
				partners.setdefault(r["shift"], []).append(r["block"])
	for s in shifts:
		s["other_blocks"] = sorted(partners.get(s["shift"], []))

	# Today's run-sheet cycles for this block's shifts (the retired weekly
	# Irrigation Planner is history only).
	planners = []
	if names:
		planners = frappe.db.sql(
			"""
			SELECT r.name, r.shift AS block, r.status, r.cycle_no, r.cycles, r.planned_start AS scheduled_start,
			       r.planned_end AS scheduled_end, r.planned_hours AS shift_hours, r.net_mm, r.reason, r.kind,
			       rs.name AS run_sheet
			FROM `tabIrrigation Run` r JOIN `tabIrrigation Run Sheet` rs ON rs.name = r.parent
			WHERE r.parenttype = 'Irrigation Run Sheet' AND rs.date = %(d)s AND r.shift IN %(s)s
			ORDER BY r.planned_start IS NULL, r.planned_start
			""",
			{"d": frappe.utils.getdate(), "s": tuple(names)},
			as_dict=True,
		)

	valves = frappe.get_all(
		"Tank And Valve", filters={"asset_type": "Valve", "block": block},
		fields=["name", "asset_label"], order_by="asset_label asc",
	)

	irro = frappe.get_all(
		"Irrometer Reading", filters={"irrigation_block": block},
		fields=["date", "irrometer_1ft_reading", "irrometer_2ft_reading"],
		order_by="date desc", limit=1,
	)

	water = None
	try:
		from upande_irrigation.api.balance import state

		water = state(wh.get("custom_farm")).get(block)
	except Exception:
		frappe.log_error(title=f"Upande Irrigation — block water state for {block}")

	return {
		"water": water,
		"block": {
			"name": wh["name"],
			"label": wh.get("warehouse_name") or wh["name"],
			"section": wh.get("parent_warehouse"),
			"farm": wh.get("custom_farm"),
			"area_ha": wh.get("custom_area_ha"),
			"type": wh.get("warehouse_type"),
		},
		"shifts": shifts,
		"planners": planners,
		"valves": valves,
		"irrometer": irro[0] if irro else None,
	}
