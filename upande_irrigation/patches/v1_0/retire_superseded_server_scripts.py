"""Disable the Server Scripts the Python engine replaced.

They were never retired when the logic moved into app source, so both copies ran:

  · "Irrigation Planner — Compute Shift" (DocType Event, Before Save) fired
    alongside events.irrigation_planner.compute_shift. Whichever ran second won,
    and the Server Script still rebuilt the irrigation_calculations child table —
    so once that table was dropped, every planner save raised AttributeError.
  · "Weekly Irrigation Scheduler" (Cron 0 6 * * 5) and "Irrigation Scheduler Run"
    (API) duplicated api/scheduler.py on the same schedule as the hooks.py cron.
    Friday 06:00 generated the week twice, once with the superseded logic.

Disabled rather than deleted: a disabled Server Script does not execute, and
keeping the row preserves the record of what the old logic actually was.
"""

import frappe

SUPERSEDED = {
	"Irrigation Planner — Compute Shift": "events.irrigation_planner.compute_shift",
	"Irrigation Scheduler Run": "api.scheduler.run",
	"Weekly Irrigation Scheduler": "scheduled.weekly.run_weekly_scheduler",
	# Already disabled, listed so the set is complete and re-enabling one is a
	# visible decision rather than an accident.
	"Irrigation Planner": "events.irrigation_planner.compute_shift",
	"Irrigation Planner — Weekly Generator": "scheduled.weekly.run_weekly_scheduler",
}


def execute():
	if not frappe.db.exists("DocType", "Server Script"):
		return

	for name, replacement in SUPERSEDED.items():
		if not frappe.db.exists("Server Script", name):
			continue
		if frappe.db.get_value("Server Script", name, "disabled"):
			continue
		frappe.db.set_value(
			"Server Script",
			name,
			"disabled",
			1,
			update_modified=False,
		)
		print(f"Disabled Server Script '{name}' — superseded by upande_irrigation.{replacement}")

	frappe.db.commit()
	frappe.clear_cache()
