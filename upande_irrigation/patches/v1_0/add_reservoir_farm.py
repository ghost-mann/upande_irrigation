"""Reservoir Pumping Record gained a farm. On a single-farm site every existing
record is that farm's; on a multi-farm site they stay blank (shown as Unassigned)
until someone sets them."""

import frappe


def execute():
	farms = frappe.get_all("Farm", pluck="name")
	if len(farms) != 1:
		return
	frappe.db.sql(
		"""UPDATE `tabReservoir Pumping Record` SET farm = %s WHERE IFNULL(farm, '') = ''""",
		farms[0],
	)
