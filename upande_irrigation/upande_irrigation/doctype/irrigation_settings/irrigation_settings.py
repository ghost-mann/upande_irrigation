"""Controller for Irrigation Settings."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


class IrrigationSettings(Document):
	def validate(self):
		self.validate_expected_rainfall()

	def validate_expected_rainfall(self):
		"""One row per farm, and no negative expectation."""
		seen = set()
		for row in self.get("rain_expected") or []:
			if row.farm in seen:
				frappe.throw(_("Expected rainfall: {0} is listed twice (row {1}).").format(row.farm, row.idx))
			seen.add(row.farm)
			for m in MONTHS:
				if flt(row.get(m)) < 0:
					frappe.throw(_("Expected rainfall for {0} in {1} cannot be negative (row {2}).").format(
						row.farm, m.capitalize(), row.idx))
