"""Morning run sheet — the hourly cron generates today's sheet once, at the
Irrigation Scheduler's run hour.

Hourly rather than a fixed daily cron so the hour stays a setting (run_hour on
Irrigation Scheduler) instead of a code change. Replaces the Friday weekly job.
"""

import frappe

GUARD_KEY = "upande_irrigation:runsheet_generated"


def maybe_run():
	cfg = frappe.get_cached_doc("Irrigation Scheduler")
	if not cfg.enabled or not cfg.auto_run_enabled:
		return
	now = frappe.utils.now_datetime()
	if now.hour != int(cfg.run_hour if cfg.run_hour not in (None, "") else 5):
		return
	today = str(now.date())
	if frappe.cache.get_value(GUARD_KEY) == today:
		return
	frappe.cache.set_value(GUARD_KEY, today, expires_in_sec=86400)
	try:
		from upande_irrigation.api.runsheet import generate

		frappe.set_user("Administrator")
		generate()
	except Exception:
		frappe.log_error(title="Upande Irrigation — morning run sheet failed")
