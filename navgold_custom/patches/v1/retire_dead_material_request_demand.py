# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt

from navgold_custom.navgold_custom.custom_script.material_request.material_request import (
	get_open_material_requests,
	stop_material_requests_without_live_demand,
)


def execute():
	open_material_requests = get_open_material_requests()
	if not open_material_requests:
		return

	outstanding_qty = get_outstanding_qty(open_material_requests)
	stopped = stop_material_requests_without_live_demand(open_material_requests)
	if not stopped:
		return

	released_qty = sum(outstanding_qty.get(name, 0) for name in stopped)
	print(
		f"Stopped {len(stopped)} of {len(open_material_requests)} open Material Requests "
		f"whose Sales Orders are already settled, dropping {released_qty} units of dead demand."
	)


def get_outstanding_qty(material_requests):
	outstanding_qty = {}
	for row in frappe.get_all(
		"Material Request Item",
		filters={"parent": ("in", material_requests), "docstatus": 1},
		fields=["parent", "stock_qty", "ordered_qty"],
	):
		pending = max(flt(row.stock_qty) - flt(row.ordered_qty), 0)
		outstanding_qty[row.parent] = outstanding_qty.get(row.parent, 0) + pending

	return outstanding_qty
