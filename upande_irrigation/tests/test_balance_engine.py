"""The daily soil water balance (FAO-56, adapted for micro-irrigated avocado).

The bugs being locked out, all found in the weekly planner it replaces:
- rain counted twice (pan evaporation already contains it) and fully effective;
- no soil memory, so a wet week erased demand and a dry spell never accumulated;
- coverage dividing the hours (it raised them) instead of reducing crop use;
- a division by zero wherever a block had no emitter data.
"""

from frappe.tests.utils import FrappeTestCase

from upande_irrigation.engine import balance as B

BANDS = [
	{"age_from": 0, "age_to": 2, "root_depth_m": 0.3, "canopy_pct": 20, "kc": [0.45] * 12},
	{"age_from": 3, "age_to": 4, "root_depth_m": 0.45, "canopy_pct": 45, "kc": [0.60] * 12},
	{"age_from": 5, "age_to": 999, "root_depth_m": 0.6, "canopy_pct": 70, "kc": [0.80, 0.80, 0.80] + [0.75] * 9},
]
POINTS = [(0, 0.0), (10, 0.1), (30, 0.5), (60, 0.8), (100, 1.0)]


class TestFormulas(FrappeTestCase):
	def test_epan_is_rain_plus_the_refill(self):
		# 25.4 mm rain, 41 cups taken out of the pan: 4.9 mm actually evaporated.
		self.assertAlmostEqual(B.epan(25.4, -41, 0.5), 4.9)
		self.assertAlmostEqual(B.epan(0, 8, 0.5), 4.0)

	def test_epan_never_negative(self):
		self.assertEqual(B.epan(0, -10, 0.5), 0.0)

	def test_kc_by_age_band_and_month(self):
		self.assertEqual(B.kc_for(1, 6, BANDS), 0.45)
		self.assertEqual(B.kc_for(12, 2, BANDS), 0.80)
		self.assertEqual(B.kc_for(12, 7, BANDS), 0.75)

	def test_kr_reduces_use_under_partial_cover(self):
		self.assertAlmostEqual(B.kr(70), 0.85)
		self.assertAlmostEqual(B.kr(100), 1.0)
		self.assertAlmostEqual(B.kr(0), 0.5)

	def test_taw(self):
		self.assertAlmostEqual(B.taw(160, 0.6), 96.0)

	def test_rate_from_trees_and_emitters(self):
		# 864 trees × 1 × 70 L/h on 3.4 ha = 60 480 L/h over 34 000 m² = 1.78 mm/h
		self.assertAlmostEqual(B.rate_mm_hr(864, 1, 70, 3.4), 1.7788, places=3)

	def test_rate_is_zero_not_an_error_without_data(self):
		self.assertEqual(B.rate_mm_hr(0, 1, 70, 3.4), 0.0)
		self.assertEqual(B.rate_mm_hr(864, 1, 70, 0), 0.0)

	def test_effective_rain_loses_small_events_and_is_capped_by_room(self):
		self.assertEqual(B.effective_rain(1.5, 2.0, 0.9, room=50), 0.0)
		self.assertAlmostEqual(B.effective_rain(12, 2.0, 0.9, room=50), 9.0)
		# only 4 mm of room left in the root zone: the rest drains
		self.assertAlmostEqual(B.effective_rain(40, 2.0, 0.9, room=4), 4.0)

	def test_step_is_clamped_to_the_root_zone(self):
		self.assertAlmostEqual(B.step(10, 5, 0, 0, 96), 15)
		self.assertEqual(B.step(10, 5, 30, 0, 96), 0.0)
		self.assertEqual(B.step(94, 5, 0, 0, 96), 96)
		self.assertAlmostEqual(B.step(40, 5, 0, 20, 96), 25)

	def test_tension_fraction_interpolates(self):
		self.assertAlmostEqual(B.tension_fraction(20, POINTS), 0.3)
		self.assertAlmostEqual(B.tension_fraction(150, POINTS), 1.0)

	def test_blend_moves_depletion_toward_the_irrometer(self):
		# model says 10 mm, irrometer at 30 cb says 0.5 × 96 = 48 mm; w = 0.5 → 29
		self.assertAlmostEqual(B.blend(10, 30, 96, POINTS, 0.5), 29.0)


PARAMS = {
	"kpan": 0.75, "depth_per_cup_mm": 0.5, "rain_loss_mm": 2.0, "rain_efficiency": 0.9,
	"bands": BANDS, "tension_points": POINTS, "irrometer_weight": 0.5,
}
PROFILE = {"age_years": 12, "awc_mm_per_m": 160, "root_depth_m": 0.6, "canopy_pct": 70,
           "rate_mm_hr": 1.78, "application_efficiency": 0.9, "depletion_fraction": 0.5}


class TestSimulate(FrappeTestCase):
	def day(self, d, rain=0.0, cups=10, irr_hours=0.0, cb=None):
		return {"date": f"2026-07-{d:02d}", "month": 7, "rain": rain, "cups": cups,
		        "irrigation_hours": irr_hours, "irrometer_cb": cb, "estimated": False}

	def test_a_dry_spell_accumulates(self):
		# Epan 5 mm × 0.75 × 0.75 × 0.85 = 2.39 mm/day
		out = B.simulate([self.day(i) for i in range(1, 11)], PROFILE, PARAMS)
		self.assertAlmostEqual(out[-1]["depletion_mm"], 23.9, places=1)
		self.assertAlmostEqual(out[-1]["taw_mm"], 96.0)
		self.assertAlmostEqual(out[-1]["raw_mm"], 48.0)

	def test_rain_refills_but_surplus_is_not_banked(self):
		days = [self.day(i) for i in range(1, 6)] + [self.day(6, rain=80, cups=-150)]
		out = B.simulate(days, PROFILE, PARAMS)
		self.assertEqual(out[-1]["depletion_mm"], 0.0)
		nxt = B.simulate(days + [self.day(7)], PROFILE, PARAMS)
		self.assertAlmostEqual(nxt[-1]["depletion_mm"], 2.39, places=1)

	def test_recorded_irrigation_reduces_depletion(self):
		days = [self.day(i) for i in range(1, 11)]
		days[-1]["irrigation_hours"] = 10  # 10 h × 1.78 × 0.9 = 16.0 mm net
		out = B.simulate(days, PROFILE, PARAMS)
		self.assertAlmostEqual(out[-1]["irrigation_mm"], 16.02, places=1)
		self.assertAlmostEqual(out[-1]["depletion_mm"], 23.9 - 16.02, places=1)

	def test_irrometer_corrects_the_model(self):
		days = [self.day(i) for i in range(1, 4)]
		days[-1]["irrometer_cb"] = 30
		out = B.simulate(days, PROFILE, PARAMS)
		self.assertTrue(out[-1]["irrometer_adjusted"])
		self.assertGreater(out[-1]["depletion_mm"], out[-2]["depletion_mm"] + 10)

	def test_project_finds_the_trigger_day(self):
		p = B.project(40.0, 2.39, 48.0, horizon=7)
		self.assertEqual(p["trigger_day"], 4)  # 40 + 4×2.39 = 49.6 ≥ 48
		self.assertEqual(len(p["depletion_by_day"]), 7)

	def test_project_when_already_past_raw(self):
		self.assertEqual(B.project(50.0, 2.39, 48.0, horizon=7)["trigger_day"], 0)

	def test_hours_to_refill(self):
		# 48 mm net ÷ 0.9 ÷ 1.78 mm/h = 29.96 h
		self.assertAlmostEqual(B.hours_to_refill(48, 1.78, 0.9), 29.96, places=1)
		self.assertEqual(B.hours_to_refill(48, 0, 0.9), 0.0)
