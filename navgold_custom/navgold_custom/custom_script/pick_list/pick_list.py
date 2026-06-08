# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from navgold_custom.navgold_custom.erpnext_compat import create_delivery_note


def sort_item_locations_by_walk_route(doc, method):
	doc.locations.sort(key=lambda x: x.custom_walk_route or 0)


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
