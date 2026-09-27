"""Turning block states into a day's run sheet: which shifts, how long, when.

Locks out: a shift planned from its wettest block instead of its driest; a pump
double-booked; cycles of one shift run back to back; work silently dropped when the
windows fill; a pump with no window treated as a 24-hour pump.
"""

import datetime

from frappe.tests.utils import FrappeTestCase

from upande_irrigation.engine import runsheet as R

SETTINGS = {"min_run_hours": 0.5, "max_run_hours_per_day": 12, "irrigate_ahead_days": 1,
            "auto_cycle_threshold_hrs": 5, "cycles_when_below_threshold": 1,
            "cycles_when_above_threshold": 2, "cycle_rest_hours": 2}
DAY = datetime.date(2026, 9, 28)


def block(d, raw=48, etc=2.4, rate=1.78, trigger=None, eff=0.9, flow=60):
	return {"depletion_mm": d, "raw_mm": raw, "taw_mm": raw * 2, "mean_etc_mm": etc, "rate_mm_hr": rate,
	        "application_efficiency": eff, "trigger_day": trigger, "block_flow_m3_hr": flow, "label": "b"}


def at(h, m=0):
	return datetime.datetime.combine(DAY, datetime.time(h, m))


class TestShiftNeed(FrappeTestCase):
	def test_the_driest_block_sets_the_run(self):
		need = R.shift_need([block(20), block(47, trigger=1)], SETTINGS)
		self.assertEqual(need["kind"], "Due")
		# end of today: 47 + 2.4 = 49.4 mm net → ÷ 0.9 ÷ 1.78 = 30.8 h, capped at 12
		self.assertAlmostEqual(need["hours"], 12.0)
		self.assertAlmostEqual(need["net_mm"], 49.4, places=1)
		self.assertTrue(any("capped" in r for r in need["reasons"]))

	def test_not_due_is_none(self):
		self.assertIsNone(R.shift_need([block(10, trigger=None)], SETTINGS)["kind"])

	def test_ahead_within_the_window(self):
		self.assertEqual(R.shift_need([block(44, trigger=2)], SETTINGS)["kind"], "Ahead")

	def test_a_block_without_a_rate_is_reported_not_divided(self):
		need = R.shift_need([block(50, trigger=0, rate=0)], SETTINGS)
		self.assertEqual(need["hours"], 0)
		self.assertTrue(any("application rate" in r for r in need["reasons"]))

	def test_urgency_is_depletion_over_raw(self):
		self.assertAlmostEqual(R.shift_need([block(47, trigger=1)], SETTINGS)["urgency"], 49.4 / 48, places=2)


class TestPlace(FrappeTestCase):
	def req(self, shift, hours, urgency, kind="Due"):
		return {"shift": shift, "section": "S", "hours": hours, "net_mm": 10, "urgency": urgency, "kind": kind, "reasons": []}

	def test_one_shift_at_a_time_most_urgent_first(self):
		rows = R.place({"P1": [self.req("A", 3, 1.0), self.req("B", 3, 1.4)]}, {"P1": [(at(6), at(18))]}, SETTINGS)
		placed = [r for r in rows if r["status"] == "Planned"]
		self.assertEqual([r["shift"] for r in placed], ["B", "A"])
		self.assertEqual(placed[0]["planned_start"], at(6))
		self.assertEqual(placed[1]["planned_start"], at(9))

	def test_cycles_rest_and_the_pump_fills_the_gap(self):
		rows = R.place({"P1": [self.req("A", 8, 1.4), self.req("B", 2, 1.0)]}, {"P1": [(at(6), at(20))]}, SETTINGS)
		a = [r for r in rows if r["shift"] == "A"]
		b = [r for r in rows if r["shift"] == "B"][0]
		self.assertEqual(len(a), 2)  # 8 h > 5 h threshold → 2 × 4 h
		self.assertEqual(a[0]["planned_end"], at(10))
		self.assertGreaterEqual(a[1]["planned_start"], at(12))  # 2 h rest
		self.assertEqual(b["planned_start"], at(10))  # B uses the pump while A rests

	def test_overflow_is_not_placed_never_dropped(self):
		rows = R.place({"P1": [self.req("A", 4, 1.4), self.req("B", 4, 1.2)]}, {"P1": [(at(6), at(12))]}, SETTINGS)
		b = [r for r in rows if r["shift"] == "B"]
		self.assertEqual(b[0]["status"], "Not placed")
		self.assertAlmostEqual(b[0]["planned_hours"], 4)

	def test_no_window_places_nothing(self):
		rows = R.place({"P1": [self.req("A", 2, 1.4)]}, {"P1": []}, SETTINGS)
		self.assertEqual(rows[0]["status"], "Not placed")
		self.assertIn("run window", rows[0]["reason"])

	def test_ahead_only_takes_spare_time(self):
		rows = R.place({"P1": [self.req("A", 2, 0.95, kind="Ahead"), self.req("B", 5, 1.1)]}, {"P1": [(at(6), at(12))]}, SETTINGS)
		placed = [r["shift"] for r in rows if r["status"] == "Planned"]
		self.assertEqual(placed, ["B"])  # B (Due) takes 06–11; A (Ahead, 2 h) does not fit the last hour
		self.assertEqual([r["status"] for r in rows if r["shift"] == "A"], ["Not placed"])

	def test_a_cycle_never_straddles_two_windows(self):
		rows = R.place({"P1": [self.req("A", 3, 1.4)]}, {"P1": [(at(6), at(8)), (at(14), at(18))]}, SETTINGS)
		self.assertEqual(rows[0]["planned_start"], at(14))


class TestWindows(FrappeTestCase):
	def test_every_day_and_overnight(self):
		w = R.windows_for_date(DAY, [{"day": "Every day", "start_time": "20:00:00", "end_time": "04:00:00"}], "")
		self.assertEqual(w, [(at(20), at(4) + datetime.timedelta(days=1))])

	def test_weekday_filter_and_rest_dates(self):
		rows = [{"day": "Monday", "start_time": "06:00:00", "end_time": "12:00:00"},
		        {"day": "Tuesday", "start_time": "06:00:00", "end_time": "18:00:00"}]
		self.assertEqual(R.windows_for_date(DAY, rows, ""), [(at(6), at(12))])  # 2026-09-28 is a Monday
		self.assertEqual(R.windows_for_date(DAY, rows, "2026-09-28\n2026-12-25"), [])


class TestPlaceAroundExistingWork(FrappeTestCase):
	"""Regeneration mid-day: never on top of a cycle already on the sheet, never in
	the past, and overlapping windows must not double-book the pump."""

	def req(self, shift, hours, urgency, kind="Due"):
		return {"shift": shift, "section": "S", "hours": hours, "net_mm": 10, "urgency": urgency, "kind": kind, "reasons": []}

	def test_busy_intervals_are_avoided(self):
		rows = R.place({"P1": [self.req("B", 3, 1.4)]}, {"P1": [(at(6), at(18))]}, SETTINGS, busy={"P1": [(at(6), at(10))]})
		self.assertEqual(rows[0]["planned_start"], at(10))

	def test_nothing_is_placed_in_the_past(self):
		rows = R.place({"P1": [self.req("B", 3, 1.4)]}, {"P1": [(at(6), at(18))]}, SETTINGS, not_before=at(15))
		self.assertEqual(rows[0]["planned_start"], at(15))
		late = R.place({"P1": [self.req("C", 4, 1.4)]}, {"P1": [(at(6), at(18))]}, SETTINGS, not_before=at(15))
		self.assertEqual(late[0]["status"], "Not placed")

	def test_overlapping_windows_are_merged(self):
		w = R.windows_for_date(DAY, [{"day": "Every day", "start_time": "06:00:00", "end_time": "18:00:00"},
		                             {"day": "Monday", "start_time": "08:00:00", "end_time": "12:00:00"}])
		self.assertEqual(w, [(at(6), at(18))])
		rows = R.place({"P1": [self.req(s, 3, 1.4 - i / 10) for i, s in enumerate("ABCD")]}, {"P1": w}, SETTINGS)
		spans = sorted((r["planned_start"], r["planned_end"]) for r in rows if r["status"] == "Planned")
		for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
			self.assertLessEqual(a1, b0)

	def test_a_shift_with_no_rate_is_reported_not_dropped(self):
		rows = R.place({"P1": [{**self.req("Z", 0, 1.4), "reasons": ["no application rate"]}]}, {"P1": [(at(6), at(18))]}, SETTINGS)
		self.assertEqual(rows[0]["status"], "Not placed")
		self.assertIn("no application rate", rows[0]["reason"])
