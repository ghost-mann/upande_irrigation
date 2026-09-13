"""Controller for Irrigation Shift Block."""

import frappe
from frappe.model.document import Document


class IrrigationShiftBlock(Document):
	def validate(self):
		"""A shift waters blocks, never a whole section.

		The live v15 data has three rows naming 23HA_SECTION - KL, which is a group
		warehouse holding twelve blocks. Letting that through would have the engine
		plan one shift for an entire section.
		"""
		if self.block and frappe.db.get_value("Warehouse", self.block, "is_group"):
			frappe.throw(
				f"{self.block} is a group warehouse. A shift block must be a leaf warehouse."
			)
