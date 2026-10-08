"""The weekly water balance per section — the farm's weekly figures, read from the
daily balance rather than computed beside it.

Per section per week (Monday start), every mm figure is the area-weighted mean over
the section's blocks: Σ_blocks(area × Σ_days mm) ÷ Σ area. When the blocks have no
area recorded, a plain mean.

    deficit_mm        ET crop − effective rain: the week's net demand in the
                      farm's own terms. It can be negative (a wet week).
    end_depletion_mm  the root-zone depletion on the week's last day — the soil's
                      memory, which a weekly deficit on its own does not carry.
    hours_planned     run-sheet cycles that were to run (Planned, Running, Done, Partial)
    hours_applied     Done at its actual hours (else planned), Partial at its actual hours

The Budget tab and the Weekly Irrigation Summary report both read summary().
"""

import frappe

MM = ("epan_mm", "etc_mm", "rain_mm", "effective_rain_mm", "irrigation_mm")


def _week(d):
	d = frappe.utils.getdate(d)
	return frappe.utils.add_days(d, -d.weekday())


@frappe.whitelist()
def summary(farm=None, section=None, from_date=None, to_date=None):
	today = frappe.utils.getdate()
	to_date = frappe.utils.getdate(to_date) if to_date else today
	# Whole weeks only: a mid-week start would label a 2-day stub as that week.
	from_date = _week(from_date) if from_date else frappe.utils.add_days(_week(today), -7 * 7)

	pf = {}
	if farm:
		pf["farm"] = farm
	if section:
		pf["section"] = section
	profiles = {p.block: p for p in frappe.get_all("Irrigation Block Profile", filters=pf,
	                                                fields=["block", "farm", "section", "area_ha"], limit_page_length=0)}
	balance = frappe.get_all(
		"Irrigation Block Balance",
		filters={"block": ["in", list(profiles) or [""]], "date": ["between", [from_date, to_date]]},
		fields=["block", "date", "depletion_mm", *MM],
		order_by="date asc",
		limit_page_length=0,
	)

	# (section, week) → block → {sums, last depletion, days}
	acc = {}
	for r in balance:
		p = profiles[r.block]
		key = (p.section, str(_week(r.date)))
		b = acc.setdefault(key, {"farm": p.farm, "blocks": {}}).setdefault("blocks", {}).setdefault(
			r.block, {"area": float(p.area_ha or 0), "days": set(), "last": None, **{m: 0.0 for m in MM}})
		for m in MM:
			b[m] += float(r.get(m) or 0)
		b["days"].add(str(r.date))
		b["last"] = float(r.depletion_mm or 0)  # ordered by date: the last seen is the week's last day

	hours = _hours(farm, section, from_date, to_date)
	for key in hours:
		acc.setdefault(key, {"farm": hours[key]["farm"], "blocks": {}})

	rows = []
	for (sec, week), v in sorted(acc.items(), key=lambda kv: (kv[0][0] or "", kv[0][1])):
		blocks = list(v["blocks"].values())
		total = sum(b["area"] for b in blocks)
		weight = (lambda b: b["area"] / total) if total > 0 else (lambda b: 1.0 / len(blocks))
		row = {"section": sec, "farm": v["farm"], "week": week,
		       "days": max((len(b["days"]) for b in blocks), default=0)}
		for m in MM:
			row[m] = round(sum(weight(b) * b[m] for b in blocks), 1) if blocks else None
		row["deficit_mm"] = round(row["etc_mm"] - row["effective_rain_mm"], 1) if blocks else None
		row["end_depletion_mm"] = round(sum(weight(b) * (b["last"] or 0) for b in blocks), 1) if blocks else None
		h = hours.get((sec, week), {})
		row["hours_planned"] = round(h.get("planned", 0.0), 1)
		row["hours_applied"] = round(h.get("applied", 0.0), 1)
		rows.append(row)
	return {"from": str(from_date), "to": str(to_date), "rows": rows}


def _hours(farm, section, from_date, to_date):
	cond = ""
	if farm:
		cond += " AND s.farm = %(farm)s"
	if section:
		cond += " AND r.section = %(section)s"
	runs = frappe.db.sql(
		f"""
		SELECT s.farm, s.date, r.section, r.status, r.planned_hours, r.actual_hours
		FROM `tabIrrigation Run` r
		INNER JOIN `tabIrrigation Run Sheet` s ON s.name = r.parent
		WHERE r.parenttype = 'Irrigation Run Sheet' AND s.date BETWEEN %(from)s AND %(to)s
		  AND r.status IN ('Planned', 'Running', 'Done', 'Partial') {cond}
		""",
		{"farm": farm, "section": section, "from": from_date, "to": to_date},
		as_dict=True,
	)
	out = {}
	for r in runs:
		h = out.setdefault((r.section, str(_week(r.date))), {"farm": r.farm, "planned": 0.0, "applied": 0.0})
		planned, actual = float(r.planned_hours or 0), float(r.actual_hours or 0)
		h["planned"] += planned
		if r.status == "Done":
			h["applied"] += actual or planned
		elif r.status == "Partial":
			h["applied"] += actual
	return out
