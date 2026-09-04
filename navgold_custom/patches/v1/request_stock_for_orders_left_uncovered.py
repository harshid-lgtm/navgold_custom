# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt

from navgold_custom.navgold_custom.amazon_repository import CLOSED_ORDER_STATUSES
from navgold_custom.navgold_custom.custom_script.sales_order.sales_order import (
	create_material_request,
	get_shelf_qty_map,
)

# Amazon has already handed these to the carrier, whatever ERPNext's delivery state says — several
# carry per_delivered = 0 because the Delivery Note was never made. Requesting stock for them would
# buy replacements for goods the customer already has.
SHIPPED_ORDER_STATUSES = ("Shipped", "PartiallyShipped")


def execute():
	sales_orders = frappe.get_all(
		"Sales Order",
		filters={
			"docstatus": 1,
			"fulfillment_channel": "MFN",
			"status": ("not in", ("Closed", "Completed", "Cancelled")),
			"amazon_order_status": ("not in", SHIPPED_ORDER_STATUSES + CLOSED_ORDER_STATUSES),
		},
		pluck="name",
	)
	if not sales_orders:
		return

	candidates = get_orders_short_of_stock(sales_orders)
	if not candidates:
		return

	requested = []
	requested_qty = 0.0
	for name in candidates:
		# create_material_request needs the whole document, so this is one read per candidate — which
		# is why the shelf screen above runs first rather than walking every open order.
		material_request = create_material_request(frappe.get_doc("Sales Order", name))
		if not material_request:
			continue

		requested.append(material_request.name)
		requested_qty += sum(flt(row.stock_qty) for row in material_request.items)

	if not requested:
		return

	print(
		f"Raised {len(requested)} Material Requests for {requested_qty:g} units across "
		f"{len(candidates)} of {len(sales_orders)} open orders that were left uncovered by supply "
		"raised for a different order."
	)


def get_orders_short_of_stock(sales_orders: list[str]) -> list[str]:
	"""Orders holding a line for an item whose shelf is already in deficit, still short of its own
	request. A deliberately generous screen: an item with stock to spare would raise nothing anyway,
	so this only skips the reads that create_material_request would have thrown away."""
	order_items = frappe.get_all(
		"Sales Order Item",
		filters={"parent": ("in", sales_orders), "docstatus": 1},
		fields=["parent", "item_code", "stock_qty", "requested_qty"],
	)
	if not order_items:
		return []

	shelf_qty_map = get_shelf_qty_map(list({row.item_code for row in order_items}))

	candidates = {
		row.parent
		for row in order_items
		if shelf_qty_map.get(row.item_code, 0) < 0 and flt(row.stock_qty) > flt(row.requested_qty)
	}
	return [name for name in sales_orders if name in candidates]
