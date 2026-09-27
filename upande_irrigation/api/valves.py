"""Irrigation Control — valve state + geometry API.

For each Valve in `Tank And Valve` (asset_type='Valve'):

  schedule_state   ON if NOW is between an Irrigation Planner's scheduled_start
                   and scheduled_end whose shift contains the valve's block.
                   OFF otherwise.

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

	# ── Build the lookup: valve.block → active planner row ───────
	# A valve's `block` is a sub-block warehouse (e.g. "AIRSTRIP BLK 1 - KL").
	# Sub-blocks roll up to a shift via `Irrigation Shift Block`, the child
	# table of the Irrigation Scheduler single (parenttype/parentfield below
	# pin it to that table specifically, since child rows are shared storage).
	# A planner row (Irrigation Planner) holds the scheduled_start/end for
	# each shift. The active planner for a sub-block right now satisfies:
	#   sb.block = <valve.block>
	#   p.block  = sb.shift     (the shift name)
	#   p.scheduled_start <= NOW <= p.scheduled_end
	# No is_active filter: Blocks List never had one, and adding one here
	# would silently change which valves report a schedule.
	active = frappe.db.sql(
		"""
		SELECT
			sb.block          AS sub_block,
			p.name            AS planner,
			p.block           AS shift,
			p.scheduled_start AS start_dt,
			p.scheduled_end   AS end_dt,
			p.shift_hours     AS shift_hours
		FROM `tabIrrigation Shift Block` sb
		INNER JOIN `tabIrrigation Planner` p ON p.block = sb.shift
		WHERE sb.parenttype = 'Irrigation Scheduler'
		  AND sb.parentfield = 'shift_blocks'
		  AND p.docstatus < 2
		  AND p.scheduled_start IS NOT NULL
		  AND p.scheduled_end IS NOT NULL
		  AND %(now)s BETWEEN p.scheduled_start AND p.scheduled_end
		""",
		{"now": now_str},
		as_dict=True,
	)
	active_by_subblock = {row["sub_block"]: row for row in active}

	# ── Resolve next ON for OFF valves (so UI can show "Next at HH:MM") ──
	#   For each sub-block where there's NO active planner right now,
	#   find the next planner whose scheduled_start is in the future.
	off_subblocks = [v["block"] for v in valves if v["block"] and v["block"] not in active_by_subblock]
	next_by_subblock = {}
	if off_subblocks:
		upcoming = frappe.db.sql(
			"""
			SELECT sb.block          AS sub_block,
			       MIN(p.scheduled_start) AS next_start
			FROM `tabIrrigation Shift Block` sb
			INNER JOIN `tabIrrigation Planner` p ON p.block = sb.shift
			WHERE sb.parenttype = 'Irrigation Scheduler'
			  AND sb.parentfield = 'shift_blocks'
			  AND p.docstatus < 2
			  AND p.scheduled_start > %(now)s
			  AND sb.block IN %(blocks)s
			GROUP BY sb.block
			""",
			{"now": now_str, "blocks": tuple(off_subblocks)},
			as_dict=True,
		)
		next_by_subblock = {row["sub_block"]: row["next_start"] for row in upcoming}

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


@frappe.whitelist()
def set_override(valve, state):
	"""Operator override of a valve's manual state."""
	if state not in _VALID_OVERRIDES:
		frappe.throw(
			f"`state` must be one of {', '.join(_VALID_OVERRIDES)}. Got: {state!r}"
		)

	doc = frappe.get_doc("Tank And Valve", valve)
	if doc.asset_type != "Valve":
		frappe.throw(f"{valve} is not a Valve (asset_type={doc.asset_type}).")

	doc.manual_state = state
	# manual_state_set_at/by are stamped in the validate() controller hook
	doc.save(ignore_permissions=False)
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
		doc.manual_state = state
		doc.save()
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

	filters = {"disabled": 0, "is_group": 0}
	if frappe.db.exists("Warehouse Type", "Block"):
		filters["warehouse_type"] = "Block"
	if farm and meta.has_field("custom_farm"):
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
