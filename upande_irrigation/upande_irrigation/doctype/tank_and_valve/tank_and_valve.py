# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class TankAndValve(Document):
	def validate(self):
		# A valve must point at a Tank, not at another Valve
		if self.asset_type == "Valve" and self.tank:
			tank_type = frappe.db.get_value("Tank And Valve", self.tank, "asset_type")
			if tank_type and tank_type != "Tank":
				frappe.throw(
					f"`tank` must reference an asset_type=Tank record. "
					f"{self.tank} is asset_type={tank_type}."
				)

		# Stamp override metadata when manual_state changes away from Auto
		if self.asset_type == "Valve" and self.has_value_changed("manual_state"):
			if self.manual_state and self.manual_state != "Auto":
				self.manual_state_set_at = frappe.utils.now_datetime()
				self.manual_state_set_by = frappe.session.user
			else:
				self.manual_state_set_at = None
				self.manual_state_set_by = None
