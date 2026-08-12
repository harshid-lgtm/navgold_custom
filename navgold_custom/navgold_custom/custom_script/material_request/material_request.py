# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt

OPEN_STATUSES = ("Pending", "Partially Ordered", "Partially Received")
SETTLED_SALES_ORDER_STATUSES = ("Closed", "Completed")


def retire_demand_for_sales_orders(sales_orders):
	if not sales_orders:
		return []

	return stop_material_requests_without_live_demand(get_open_material_requests(sales_orders))


def stop_material_requests_without_live_demand(material_requests):
	if not material_requests:
		return []

	rows = frappe.get_all(
		"Material Request Item",
		filters={"parent": ("in", material_requests), "docstatus": 1},
		fields=["parent", "sales_order_item"],
	)
	live_sales_order_items = get_live_sales_order_items(
		{row.sales_order_item for row in rows if row.sales_order_item}
	)

	still_wanted = {
		row.parent
		for row in rows
		if not row.sales_order_item or row.sales_order_item in live_sales_order_items
	}

	stopped = []
	for name in material_requests:
		if name in still_wanted:
			continue
		material_request = frappe.get_doc("Material Request", name)
		material_request.update_status("Stopped")
		stopped.append(name)

	return stopped


def get_live_sales_order_items(sales_order_items):
	if not sales_order_items:
		return set()

	rows = frappe.get_all(
		"Sales Order Item",
		filters={"name": ("in", list(sales_order_items))},
		fields=["name", "parent", "qty", "delivered_qty"],
	)
	if not rows:
		return set()

	sales_order_state = {
		row.name: row
		for row in frappe.get_all(
			"Sales Order",
			filters={"name": ("in", list({row.parent for row in rows}))},
			fields=["name", "docstatus", "status"],
		)
	}

	live = set()
	for row in rows:
		sales_order = sales_order_state.get(row.parent)
		if not sales_order or sales_order.docstatus == 2:
			continue
		if sales_order.status in SETTLED_SALES_ORDER_STATUSES:
			continue
		if flt(row.delivered_qty) >= flt(row.qty):
			continue
		live.add(row.name)

	return live


def get_open_material_requests(sales_orders=None):
	material_request = frappe.qb.DocType("Material Request")
	material_request_item = frappe.qb.DocType("Material Request Item")

	query = (
		frappe.qb.from_(material_request)
		.inner_join(material_request_item)
		.on(material_request.name == material_request_item.parent)
		.select(material_request.name)
		.distinct()
		.where(
			(material_request.material_request_type == "Purchase")
			& (material_request.docstatus == 1)
			& material_request.status.isin(OPEN_STATUSES)
		)
	)

	if sales_orders is not None:
		query = query.where(material_request_item.sales_order.isin(list(sales_orders)))

	return query.run(pluck=True)


def retire_settled_demand():
	stop_material_requests_without_live_demand(get_open_material_requests())


def retire_demand_on_delivery_note(doc, method=None):
	if doc.is_return:
		return

	sales_orders = {row.against_sales_order for row in doc.items if row.get("against_sales_order")}
	retire_demand_for_sales_orders(list(sales_orders))
