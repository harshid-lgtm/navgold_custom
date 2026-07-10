# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe


def refresh_sales_order_reserved_qty(doc, method):
	"""ERPNext updates SRE delivered_qty on Delivery Note submit/cancel
	(selling_controller.update_stock_reservation_entries) but never recomputes
	Sales Order Item.stock_reserved_qty, so the stale aggregate makes the pick list
	creation guard block every later pick list for the same Sales Order. Recompute it."""
	if doc.is_return:
		return

	sales_order_details = {
		item.so_detail for item in doc.items if item.get("against_sales_order") and item.so_detail
	}
	if not sales_order_details:
		return

	refreshed_details = set()
	for entry in frappe.get_all(
		"Stock Reservation Entry",
		filters={
			"voucher_type": "Sales Order",
			"voucher_detail_no": ["in", list(sales_order_details)],
			"docstatus": 1,
		},
		fields=["name", "voucher_detail_no"],
	):
		# one recompute per Sales Order Item is enough: it aggregates across all its SREs
		if entry.voucher_detail_no in refreshed_details:
			continue
		refreshed_details.add(entry.voucher_detail_no)
		frappe.get_doc("Stock Reservation Entry", entry.name).update_reserved_qty_in_voucher()
