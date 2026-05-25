"""Irrigation Control — valve state API.

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
"""

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
