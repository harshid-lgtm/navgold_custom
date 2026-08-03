# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt

from navgold_custom.navgold_custom.amazon_repository import PICKABLE_ORDER_STATUSES
from navgold_custom.navgold_custom.custom_script.pick_list.pick_list import (
	drop_sales_orders_short_of_stock,
	get_stock_fulfillment,
)
from navgold_custom.navgold_custom.erpnext_compat import create_pick_list, create_stock_entry


@frappe.whitelist()
def get_open_sales_orders_for_delivery(
	customer: str | None = None, company: str | None = None, parent_warehouse: str | None = None
):
	if not company:
		frappe.throw(_("Company is required"))

	filters = {
		"docstatus": 1,
		"status": ["not in", ["Closed", "Completed", "On Hold"]],
		"company": company,
	}

	if customer:
		filters["customer"] = customer

	or_filters = [
		["amazon_order_status", "in", PICKABLE_ORDER_STATUSES],
		["amazon_order_id", "is", "not set"],
	]

	sales_orders = frappe.db.get_all(
		"Sales Order",
		filters=filters,
		or_filters=or_filters,
		fields=[
			"name",
			"customer",
			"customer_name",
			"delivery_date",
			"dispatch_date",
			"per_picked",
			"per_delivered",
			"company",
			"is_prime",
		],
		order_by="dispatch_date asc, delivery_date asc, creation desc",
	)

	sales_orders = [
		so for so in sales_orders if not (flt(so.per_picked) >= 100 and flt(so.per_delivered) >= 100)
	]

	if not sales_orders:
		return []

	already_on_draft_pick_list = set(
		frappe.get_all(
			"Pick List Item",
			filters={"sales_order": ["in", [so.name for so in sales_orders]], "docstatus": 0},
			pluck="sales_order",
			distinct=True,
		)
	)
	sales_orders = [so for so in sales_orders if so.name not in already_on_draft_pick_list]

	if not sales_orders:
		return []

	so_names = [so.name for so in sales_orders]
	so_map = {so.name: so for so in sales_orders}

	all_so_items = frappe.get_all(
		"Sales Order Item",
		filters={"parent": ["in", so_names], "docstatus": 1},
		fields=[
			"parent",
			"item_code",
			"qty",
			"picked_qty",
			"delivered_qty",
			"conversion_factor",
			"delivered_by_supplier",
		],
	)

	if not all_so_items:
		return []

	item_codes = {item.item_code for item in all_so_items if not item.delivered_by_supplier}
	weight_map = {}
	if item_codes:
		weight_data = frappe.get_all(
			"Item",
			filters={"name": ["in", item_codes]},
			fields=["name", "weight_per_unit"],
		)
		weight_map = {row.name: flt(row.weight_per_unit) for row in weight_data}

	so_stats = {}
	for item in all_so_items:
		if item.delivered_by_supplier:
			continue

		pending_qty = flt(item.qty) - max(flt(item.picked_qty), flt(item.delivered_qty))
		if pending_qty <= 0:
			continue

		if item.parent not in so_stats:
			so_stats[item.parent] = {"total_weight": 0.0, "pending_items_count": 0}

		so_stats[item.parent]["pending_items_count"] += 1

		weight_per_unit = weight_map.get(item.item_code, 0)
		if weight_per_unit:
			stock_pending_qty = pending_qty * flt(item.conversion_factor or 1)
			so_stats[item.parent]["total_weight"] += stock_pending_qty * weight_per_unit

	result = []
	for so_name, stats in so_stats.items():
		if stats["pending_items_count"] > 0:
			so = so_map[so_name]
			result.append(
				{
					"sales_order": so.name,
					"customer": so.customer_name or so.customer,
					"delivery_date": so.delivery_date,
					"dispatch_date": so.dispatch_date,
					"total_weight": round(stats["total_weight"], 2),
					"pending_items_count": stats["pending_items_count"],
					"company": so.company,
					"is_prime": so.is_prime,
				}
			)

	result.sort(
		key=lambda x: (
			x["dispatch_date"] is None,
			x["dispatch_date"] or "9999-12-31",
			x["delivery_date"] is None,
			x["delivery_date"] or "9999-12-31",
		)
	)

	fulfillment = get_stock_fulfillment([so["sales_order"] for so in result], company, parent_warehouse)
	for so in result:
		so["in_stock"] = fulfillment.get(so["sales_order"], False)

	# in-stock orders first; the sort is stable, so the dates still order each group. Fulfillment is
	# read in date order too, so the earliest order gets first claim on a contested item.
	result.sort(key=lambda x: not x["in_stock"])

	return result


@frappe.whitelist()
def map_sales_orders_to_pick_list(sales_order_names: str, target_pr_doc_json: str):
	if not sales_order_names:
		frappe.throw(_("Sales Order names are required"))

	sales_order_names = (
		frappe.parse_json(sales_order_names) if isinstance(sales_order_names, str) else sales_order_names
	)
	target_doc = (
		frappe.parse_json(target_pr_doc_json) if isinstance(target_pr_doc_json, str) else target_pr_doc_json
	)

	if isinstance(target_doc, dict):
		target_doc = frappe.get_cached_doc(target_doc)

	notice_count = len(frappe.local.message_log or [])

	for so_name in sales_order_names:
		target_doc = create_pick_list(source_name=so_name, target_doc=target_doc)

	target_doc.run_method("set_item_locations")
	del frappe.local.message_log[notice_count:]

	drop_sales_orders_short_of_stock(target_doc, sales_order_names)

	return target_doc


@frappe.whitelist()
def create_fba_transfer_stock_entry(pick_list_name: str) -> dict:
	existing = frappe.db.exists("Stock Entry", {"pick_list": pick_list_name})
	if existing:
		return frappe.get_cached_doc("Stock Entry", existing).as_dict()

	pick_list_doc = frappe.get_cached_doc("Pick List", pick_list_name)
	stock_entry = create_stock_entry(frappe.as_json(pick_list_doc.as_dict()))

	if not stock_entry:
		frappe.throw(_("Could not create Stock Entry for Pick List {0}").format(pick_list_name))

	stock_entry["stock_entry_type"] = "FBA Transfer"
	return stock_entry
