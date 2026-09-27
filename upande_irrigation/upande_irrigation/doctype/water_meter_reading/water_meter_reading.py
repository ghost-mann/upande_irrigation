"""Controller for WaterMeterReading. The arithmetic is shared with its sibling meter doctype."""

from frappe.model.document import Document

from upande_irrigation.meters import apply_meter_rules


class WaterMeterReading(Document):
	def validate(self):
		apply_meter_rules(self)
