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
	# Sub-blocks roll up to a shift via `tabBlocks List`. A planner row
	# (Irrigation Planner) holds the scheduled_start/end for each shift.
	# The active planner for a sub-block right now satisfies:
	#   bl.block = <valve.block>
	#   p.block  = bl.parent    (the shift name)
	#   p.scheduled_start <= NOW <= p.scheduled_end
	active = frappe.db.sql(
		"""
		SELECT
			bl.block          AS sub_block,
			p.name            AS planner,
			p.block           AS shift,
			p.scheduled_start AS start_dt,
			p.scheduled_end   AS end_dt,
			p.shift_hours     AS shift_hours
		FROM `tabBlocks List` bl
		INNER JOIN `tabIrrigation Planner` p ON p.block = bl.parent
		WHERE p.docstatus < 2
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
			SELECT bl.block          AS sub_block,
			       MIN(p.scheduled_start) AS next_start
			FROM `tabBlocks List` bl
			INNER JOIN `tabIrrigation Planner` p ON p.block = bl.parent
			WHERE p.docstatus < 2
			  AND p.scheduled_start > %(now)s
			  AND bl.block IN %(blocks)s
			GROUP BY bl.block
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
