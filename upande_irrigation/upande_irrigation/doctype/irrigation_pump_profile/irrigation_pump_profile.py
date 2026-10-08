"""Controller for Irrigation Pump Profile.

A Section pump drives one section's shifts and draws either from the reservoir or
straight from the river. A Reservoir intake is the river pump that fills the
reservoir; there is at most one per farm, and it serves no section."""

import frappe
from frappe.model.document import Document

INTAKE = "Reservoir intake"


class IrrigationPumpProfile(Document):
	def validate(self):
		if self.pump_role == INTAKE:
			self.irrigation_section = None
			self.water_source = None
			other = frappe.db.exists("Irrigation Pump Profile",
			                         {"farm": self.farm, "pump_role": INTAKE, "name": ["!=", self.name or ""]})
			if other:
				frappe.throw(f"{self.farm} already has a Reservoir intake ({other}). A farm has one reservoir intake.")
		elif not self.irrigation_section:
			frappe.throw("A section pump needs its Section (Warehouse).")
