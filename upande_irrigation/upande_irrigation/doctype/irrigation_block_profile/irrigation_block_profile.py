"""Irrigation Block Profile — a block's agronomic identity.

Blank fields use the farm defaults in Irrigation Settings; the derived fields
(rate, flow, TAW, RAW) are recomputed on every save through the same resolver the
water balance uses, so the form always shows what the engine will use.
"""

import frappe
from frappe.model.document import Document


class IrrigationBlockProfile(Document):
	def validate(self):
		from upande_irrigation.api.balance import resolved_profile

		for f in ("area_ha", "tree_count", "emitters_per_tree", "emitter_flow_lph", "root_depth_m"):
			if (self.get(f) or 0) < 0:
				frappe.throw(f"{self.meta.get_label(f)} cannot be negative.")
		if self.application_efficiency and not 0 < self.application_efficiency <= 1:
			frappe.throw("Application efficiency is a fraction between 0 and 1 (e.g. 0.9).")
		if self.depletion_fraction and not 0 < self.depletion_fraction < 1:
			frappe.throw("Irrigate-at depletion (p) is a fraction between 0 and 1 (e.g. 0.5).")

		r, used = resolved_profile(self.as_dict())
		self.tree_age_years = r["age_years"]
		self.application_rate_mm_hr = r["rate_mm_hr"]
		self.block_flow_m3_hr = r["block_flow_m3_hr"]
		self.awc_mm_per_m = r["awc_mm_per_m"]
		self.taw_mm = r["taw_mm"]
		self.raw_mm = r["raw_mm"]
		self.using_defaults = ", ".join(sorted(set(used)))
