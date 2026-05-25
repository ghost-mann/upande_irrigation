"""Weekly scheduler entry point — wires the Friday 06:00 cron into the planner run.

Was previously the `Weekly Irrigation Scheduler` Server Script (Scheduler Event).
Registered in hooks.py under scheduler_events.cron["0 6 * * 5"].
"""

import frappe

from upande_irrigation.api.scheduler import run as scheduler_run


def run_weekly_scheduler():
	cfg = frappe.get_doc("Irrigation Scheduler")

	if not cfg.enabled:
		frappe.logger().info("Weekly Irrigation Scheduler: disabled in config, skipping")
		return

	if not cfg.auto_run_enabled:
		frappe.logger().info("Weekly Irrigation Scheduler: auto_run_enabled is off, skipping")
		return

	try:
		scheduler_run(triggered_by="Cron")
		frappe.logger().info("Weekly Irrigation Scheduler: completed")
	except Exception as e:
		frappe.log_error(
			title="Weekly Irrigation Scheduler — Cron Failure",
			message=str(e),
		)
