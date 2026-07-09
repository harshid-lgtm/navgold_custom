# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.data import flt

from navgold_custom.navgold_custom.erpnext_compat import create_delivery_note


def sort_item_locations_by_walk_route(doc, method):
	warehouses = {row.warehouse for row in doc.locations if row.warehouse}
	if not warehouses:
		return

	walk_route_map = frappe._dict(
		frappe.get_all(
			"Warehouse",
			filters={"name": ("in", warehouses)},
			fields=["name", "custom_walk_route"],
			as_list=1,
		)
	)

	for row in doc.locations:
		row.custom_walk_route = flt(walk_route_map.get(row.warehouse))

	doc.locations.sort(key=lambda row: row.custom_walk_route or float("inf"))
	for idx, row in enumerate(doc.locations, start=1):
		row.idx = idx


def auto_reserve_stock(doc, method):
	has_sales_order_items = any(location.sales_order for location in doc.locations)
	if not has_sales_order_items:
		return

	try:
		doc.create_stock_reservation_entries(notify=True)
	except Exception:
		frappe.log_error(title=_("Auto Stock Reservation Failed for Pick List {0}").format(doc.name))
		frappe.msgprint(
			_("Stock reservation failed for Pick List {0}. Please reserve stock manually.").format(doc.name),
			indicator="orange",
			alert=True,
		)


def auto_create_delivery_notes(doc, method):
	has_sales_order_items = any(location.sales_order for location in doc.locations)
	if not has_sales_order_items:
		return

	delivery_note = create_delivery_note(doc.name)
	if delivery_note:
		frappe.msgprint(
			_("Delivery Note {0} created automatically").format(delivery_note.name),
			indicator="green",
			alert=True,
		)
