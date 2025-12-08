# Copyright (c) 2025, Navgold and contributors
# License: MIT License

import json

import frappe
from erpnext.selling.doctype.sales_order.sales_order import create_pick_list
from frappe import _
from frappe.utils import flt


@frappe.whitelist()
def get_open_sales_orders_for_delivery(customer=None, company=None):
	if not company:
		frappe.throw(_("Company is required"))

	filters = {
		"docstatus": 1,
		"status": ["not in", ["Closed", "Completed", "On Hold"]],
		"company": company,
	}

	if customer:
		filters["customer"] = customer

	sales_orders = frappe.db.get_all(
		"Sales Order",
		filters=filters,
		fields=[
			"name",
			"customer",
			"customer_name",
			"delivery_date",
			"per_picked",
			"per_delivered",
			"company",
		],
		order_by="delivery_date asc, creation desc",
	)

	result = []

	for so in sales_orders:
		if flt(so.per_picked) >= 100 and flt(so.per_delivered) >= 100:
			continue

		so_items = frappe.db.get_all(
			"Sales Order Item",
			filters={
				"parent": so.name,
				"docstatus": 1,
			},
			fields=[
				"name",
				"item_code",
				"qty",
				"picked_qty",
				"delivered_qty",
				"conversion_factor",
				"delivered_by_supplier",
			],
		)

		if not so_items:
			continue

		total_weight = 0.0
		pending_items_count = 0

		for item in so_items:
			if item.delivered_by_supplier:
				continue

			picked_qty = flt(item.picked_qty or 0)
			delivered_qty = flt(item.delivered_qty or 0)
			ordered_qty = flt(item.qty or 0)

			pending_qty = ordered_qty - max(picked_qty, delivered_qty)

			if pending_qty <= 0:
				continue

			pending_items_count += 1

			stock_pending_qty = pending_qty * flt(item.conversion_factor or 1)

			# Get weight per unit from Item master
			weight_per_unit = frappe.get_cached_value("Item", item.item_code, "weight_per_unit")

			if weight_per_unit:
				item_weight = stock_pending_qty * flt(weight_per_unit)
				total_weight += item_weight

		if pending_items_count > 0:
			result.append(
				{
					"sales_order": so.name,
					"customer": so.customer_name or so.customer,
					"delivery_date": so.delivery_date,
					"total_weight": round(total_weight, 2),
					"pending_items_count": pending_items_count,
					"company": so.company,
				}
			)

	result.sort(key=lambda x: (x["delivery_date"] is None, x["delivery_date"] or "9999-12-31"))

	return result


@frappe.whitelist()
def map_sales_orders_to_pick_list(sales_order_names, target_pr_doc_json):
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

	for so_name in sales_order_names:
		target_doc = create_pick_list(source_name=so_name, target_doc=target_doc)

	target_doc.run_method("set_item_locations")

	return target_doc


def sort_item_locations_by_walk_route(doc, method):
	doc.locations.sort(key=lambda x: x.custom_walk_route or 0)
