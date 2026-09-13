"""Controller for Irrigation Shift Block."""

import frappe
from frappe.model.document import Document


def _reject_group_warehouse_block(block: str) -> None:
	"""Raise ValidationError if *block* names a group warehouse.

	The live v15 data has three rows naming 23HA_SECTION - KL, which is a group
	warehouse holding twelve blocks. Letting that through would have the engine
	plan one shift for an entire section.
	"""
	if block and frappe.db.get_value("Warehouse", block, "is_group"):
		frappe.throw(
			f"{block} is a group warehouse. A shift block must be a leaf warehouse."
		)


class IrrigationShiftBlock(Document):
	def validate(self):
		"""A shift waters blocks, never a whole section."""
		_reject_group_warehouse_block(self.block)


def validate_shift_blocks(doc, method=None) -> None:
	"""doc_events hook: called on Irrigation Scheduler.validate.

	Frappe doesn't propagate validate() to child rows during a parent save, so we
	re-check every row written to Irrigation Scheduler.shift_blocks here too -
	otherwise a row naming a group warehouse (e.g. the three real 23HA_SECTION - KL
	rows) would migrate silently broken.
	"""
	for row in doc.get("shift_blocks") or []:
		_reject_group_warehouse_block(row.block)
