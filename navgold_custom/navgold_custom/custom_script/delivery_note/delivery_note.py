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
		# ponytail: get_doc per Sales Order Item (update_reserved_qty_in_voucher needs a doc
		# instance, no batch API); reimplement the ERPNext aggregate by hand only if this DN
		# submit/cancel path shows up in profiling
		frappe.get_doc("Stock Reservation Entry", entry.name).update_reserved_qty_in_voucher()


def update_pick_list_completion(doc, method):
	"""Consolidation-flow pick lists deliver from the consolidation warehouse, so ERPNext's
	native per-row delivery tracking never sees their Delivery Notes and would leave them
	Open forever. Complete them (or reopen on cancel) from the Sales Order's delivery_status,
	which the native status updater has already refreshed before doc_events run."""
	# a return flips the SO back to Partly Delivered, but the pick physically completed;
	# reopening would resurrect the shelf claims the completion released
	if doc.is_return:
		return

	delivered_sales_orders = {
		item.against_sales_order for item in doc.items if item.get("against_sales_order")
	}
	if not delivered_sales_orders:
		return

	candidate_pick_lists = frappe.get_all(
		"Pick List Item",
		filters={"sales_order": ["in", list(delivered_sales_orders)], "docstatus": 1},
		distinct=True,
		pluck="parent",
	)
	if not candidate_pick_lists:
		return

	# a submitted Stock Entry linked via pick_list is the consolidation-flow fingerprint;
	# legacy pick lists keep native per-row tracking and must never be overwritten here
	consolidation_pick_lists = frappe.get_all(
		"Stock Entry",
		filters={"pick_list": ["in", candidate_pick_lists], "docstatus": 1},
		distinct=True,
		pluck="pick_list",
	)
	if not consolidation_pick_lists:
		return

	# completion is gated on ALL the pick list's sales orders, not just the ones on this
	# Delivery Note, so multi-SO pick lists only complete once every SO is fully shipped
	sales_orders_by_pick_list = {}
	for row in frappe.get_all(
		"Pick List Item",
		filters={
			"parent": ["in", consolidation_pick_lists],
			"docstatus": 1,
			"sales_order": ["is", "set"],
		},
		fields=["parent", "sales_order"],
	):
		sales_orders_by_pick_list.setdefault(row.parent, set()).add(row.sales_order)
	if not sales_orders_by_pick_list:
		return

	involved_sales_orders = set().union(*sales_orders_by_pick_list.values())
	delivery_status_map = {
		row.name: row.delivery_status
		for row in frappe.get_all(
			"Sales Order",
			filters={"name": ["in", list(involved_sales_orders)]},
			fields=["name", "delivery_status"],
		)
	}
	pick_list_status_map = {
		row.name: row.status
		for row in frappe.get_all(
			"Pick List",
			filters={"name": ["in", list(sales_orders_by_pick_list)]},
			fields=["name", "status"],
		)
	}

	for pick_list_name, pick_list_sales_orders in sales_orders_by_pick_list.items():
		fully_delivered = all(
			delivery_status_map.get(sales_order) == "Fully Delivered"
			for sales_order in pick_list_sales_orders
		)
		current_status = pick_list_status_map.get(pick_list_name)

		if doc.docstatus == 1:
			if fully_delivered and current_status != "Completed":
				# per_delivered and delivery_status must move with status: the linked Stock
				# Entry can trigger a native update_status() recompute, and status alone
				# would be reverted from the untouched per_delivered
				frappe.db.set_value(
					"Pick List",
					pick_list_name,
					{"status": "Completed", "per_delivered": 100, "delivery_status": "Fully Delivered"},
				)
		elif current_status == "Completed" and not fully_delivered:
			frappe.db.set_value(
				"Pick List",
				pick_list_name,
				{"status": "Open", "per_delivered": 0, "delivery_status": "Not Delivered"},
			)
