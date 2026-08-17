# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe

from navgold_custom.navgold_custom.amazon_repository import CLOSED_ORDER_STATUSES
from navgold_custom.navgold_custom.custom_script.material_request.material_request import (
	retire_demand_for_sales_orders,
)


def execute():
	sales_orders = frappe.get_all(
		"Sales Order",
		filters={
			"docstatus": 1,
			"amazon_order_status": ("in", list(CLOSED_ORDER_STATUSES)),
			"status": ("not in", ("Closed", "Completed")),
		},
		pluck="name",
	)
	if not sales_orders:
		return

	closed = []
	for name in sales_orders:
		sales_order = frappe.get_doc("Sales Order", name)
		sales_order.update_status("Closed")
		closed.append(name)

	stopped = retire_demand_for_sales_orders(closed)
	print(
		f"Closed {len(closed)} Sales Orders Amazon had already cancelled, releasing their reserved "
		f"stock and stopping {len(stopped)} Material Requests raised against them."
	)
