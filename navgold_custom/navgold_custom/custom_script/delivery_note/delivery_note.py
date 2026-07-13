# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe


def on_submit(doc, method):
	refresh_sales_order_reserved_qty(doc, method)
	complete_pick_list_when_sales_order_delivered(doc, method)


def on_cancel(doc, method):
	refresh_sales_order_reserved_qty(doc, method)
	complete_pick_list_when_sales_order_delivered(doc, method)


def refresh_sales_order_reserved_qty(doc, method):
	"""Recompute Sales Order Item.stock_reserved_qty, which ERPNext leaves stale on Delivery Note
	submit/cancel and would otherwise block every later pick list for the same Sales Order."""
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


def align_consolidated_bundle_packed_warehouse(doc, method):
	"""ERPNext's make_packing_list resets every packed component's warehouse from the Sales Order's
	packed item on each validate, overriding the delivery consolidation warehouse the split-bundle
	flow stages stock into. When a bundle parent row is itself sourced from that warehouse, force its
	packed components there too so they draw the staged stock instead of an empty default warehouse.
	Runs as a validate doc_event, i.e. after the controller's make_packing_list, so it wins."""
	if doc.is_return or not doc.get("packed_items"):
		return

	consolidation_warehouse = frappe.db.get_single_value(
		"Navgold Settings", "delivery_consolidation_warehouse"
	)
	if not consolidation_warehouse:
		return

	# packed_items.parent_detail_docname points at the owning Delivery Note Item's name (or idx on a
	# not-yet-named row); index both so the match holds before and after the first save
	consolidated_parent_identifiers = set()
	for row in doc.get("items"):
		if row.warehouse != consolidation_warehouse:
			continue
		for identifier in (row.name, row.idx, str(row.idx)):
			if identifier is not None:
				consolidated_parent_identifiers.add(identifier)

	if not consolidated_parent_identifiers:
		return

	for packed_item in doc.get("packed_items"):
		if packed_item.parent_detail_docname in consolidated_parent_identifiers:
			packed_item.warehouse = consolidation_warehouse


def complete_pick_list_when_sales_order_delivered(doc, method):
	"""Consolidation-flow pick lists deliver from the consolidation warehouse, so ERPNext's
	native per-row delivery tracking never sees their Delivery Notes and would leave them
	Open forever. Complete them (or reopen on cancel) from the Sales Order's delivery_status,
	which the native status updater has already refreshed before doc_events run."""
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

	consolidation_pick_lists = frappe.get_all(
		"Stock Entry",
		filters={"pick_list": ["in", candidate_pick_lists], "docstatus": 1},
		distinct=True,
		pluck="pick_list",
	)
	if not consolidation_pick_lists:
		return

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
