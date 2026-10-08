"""Weekly Irrigation Summary — per section per week: crop water use, rain, the
week's deficit, the soil's end-of-week depletion and the hours run. The same rows
as the Budget tab's weekly water balance (api.weekly.summary)."""

from frappe import _

from upande_irrigation.api.weekly import summary


def execute(filters=None):
	f = filters or {}
	rows = summary(farm=f.get("farm"), section=f.get("section"), from_date=f.get("from_date"), to_date=f.get("to_date"))["rows"]
	return columns(), rows


def columns():
	mm = lambda name, label, w=110: {"fieldname": name, "label": _(label), "fieldtype": "Float", "precision": 1, "width": w}  # noqa: E731
	return [
		{"fieldname": "week", "label": _("Week of"), "fieldtype": "Date", "width": 105},
		{"fieldname": "farm", "label": _("Farm"), "fieldtype": "Link", "options": "Farm", "width": 120},
		{"fieldname": "section", "label": _("Section"), "fieldtype": "Link", "options": "Warehouse", "width": 180},
		{"fieldname": "days", "label": _("Days"), "fieldtype": "Int", "width": 60},
		mm("epan_mm", "Pan evap. (mm)"),
		mm("etc_mm", "ET crop (mm)"),
		mm("rain_mm", "Rain (mm)", 90),
		mm("effective_rain_mm", "Effective rain (mm)", 130),
		mm("deficit_mm", "Week deficit (mm)", 125),
		mm("irrigation_mm", "Irrigated, net (mm)", 130),
		mm("end_depletion_mm", "Depletion at week end (mm)", 170),
		{"fieldname": "hours_planned", "label": _("Hours planned"), "fieldtype": "Float", "precision": 1, "width": 115},
		{"fieldname": "hours_applied", "label": _("Hours applied"), "fieldtype": "Float", "precision": 1, "width": 115},
	]
