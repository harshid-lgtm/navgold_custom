# Attribution: Code adapted from eFeone's eSeller Suite (https://github.com/efeone/eseller_suite)
# Original work licensed under the MIT License
# Copyright (c) 2024, eFeone and contributors
#
# Modifications and additions
# Copyright (c) 2025, BWH Studios and contributors
# For license information, please see license.txt

import frappe
from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
	get_sre_reserved_qty_for_items_and_warehouses as get_reserved_stock_details,
)
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Sum
from frappe.utils import cint, flt, getdate, nowdate

from navgold_custom.navgold_custom.erpnext_compat import make_material_request


def sales_order_on_submit(doc, method):
	if doc.fulfillment_channel == "MFN":
		create_material_request(doc)
	elif doc.fulfillment_channel == "AFN":
		doc.update_status("Closed")


# Based on item_dashboard.py's get_data because we need to filter out rejected warehouses
def get_item_bin_data(item_code, warehouse=None):
	Bin = DocType("Bin")
	Warehouse = DocType("Warehouse")

	query = (
		frappe.qb.from_(Bin)
		.join(Warehouse)
		.on(Bin.warehouse == Warehouse.name)
		.select(
			Bin.item_code,
			Bin.warehouse,
			Bin.projected_qty,
			Bin.reserved_qty,
			Bin.reserved_qty_for_production,
			Bin.reserved_qty_for_sub_contract,
			Bin.actual_qty,
			Bin.valuation_rate,
		)
		.where(Warehouse.is_rejected_warehouse != 1)
	)

	query = query.where(Bin.item_code == item_code)

	if warehouse:
		query = query.where(Bin.warehouse == warehouse)

	allowed_wh = frappe.get_list("Warehouse", pluck="name", ignore_permissions=False)
	if allowed_wh:
		query = query.where(Bin.warehouse.isin(allowed_wh))
	query = query.where(
		(Bin.projected_qty != 0)
		| (Bin.reserved_qty != 0)
		| (Bin.reserved_qty_for_production != 0)
		| (Bin.reserved_qty_for_sub_contract != 0)
		| (Bin.actual_qty != 0)
	)

	items = query.run(as_dict=True)

	item_code_list = [item_code] if item_code else [i["item_code"] for i in items]
	warehouse_list = [i["warehouse"] for i in items]

	sre_reserved = get_reserved_stock_details(item_code_list, warehouse_list)
	precision = cint(frappe.db.get_single_value("System Settings", "float_precision"))
	for item in items:
		item.update(
			{
				"item_name": frappe.get_cached_value("Item", item["item_code"], "item_name"),
				"stock_uom": frappe.get_cached_value("Item", item["item_code"], "stock_uom"),
				"disable_quick_entry": (
					frappe.get_cached_value("Item", item["item_code"], "has_batch_no")
					or frappe.get_cached_value("Item", item["item_code"], "has_serial_no")
				),
				"projected_qty": flt(item["projected_qty"], precision),
				"reserved_qty": flt(item["reserved_qty"], precision),
				"reserved_qty_for_production": flt(item["reserved_qty_for_production"], precision),
				"reserved_qty_for_sub_contract": flt(item["reserved_qty_for_sub_contract"], precision),
				"actual_qty": flt(item["actual_qty"], precision),
				"reserved_stock": flt(sre_reserved.get((item["item_code"], item["warehouse"]))),
			}
		)

	return items


def get_projected_qty_map(item_codes: list) -> dict:
	if not item_codes:
		return {}

	Bin = DocType("Bin")
	Warehouse = DocType("Warehouse")

	allowed_wh = frappe.get_list("Warehouse", pluck="name", ignore_permissions=False)

	query = (
		frappe.qb.from_(Bin)
		.join(Warehouse)
		.on(Bin.warehouse == Warehouse.name)
		.select(Bin.item_code, Sum(Bin.projected_qty).as_("projected_qty"))
		.where((Bin.item_code.isin(item_codes)) & (Warehouse.is_rejected_warehouse != 1))
		.groupby(Bin.item_code)
	)

	if allowed_wh:
		query = query.where(Bin.warehouse.isin(allowed_wh))

	rows = query.run(as_dict=True)
	return {row.item_code: flt(row.projected_qty) for row in rows}


def get_shortage_by_sales_order_item(doc, projected_qty_map):
	shortage_by_item = {
		item_code: abs(projected_qty)
		for item_code, projected_qty in projected_qty_map.items()
		if projected_qty < 0
	}

	today = getdate(nowdate())
	items_needing_stock = {}

	for item in doc.items:
		remaining_shortage = shortage_by_item.get(item.item_code, 0)
		if remaining_shortage <= 0:
			continue

		# The shortage is item-wide, but the Material Request line hangs off this one Sales Order
		# line and ERPNext validates it against that line's stock_qty. Asking for the whole
		# item-wide figure throws OverAllowanceError and loses the entire request, so each line
		# takes only its own share and the rest stays with the orders that caused it.
		required_qty = min(remaining_shortage, flt(item.stock_qty) - flt(item.requested_qty))
		if required_qty <= 0:
			continue

		shortage_by_item[item.item_code] = remaining_shortage - required_qty

		delivery_date = getdate(item.delivery_date) if item.delivery_date else getdate(doc.delivery_date)
		items_needing_stock[item.name] = {
			"required_qty": required_qty,
			"schedule_date": delivery_date if delivery_date > today else today,
		}

	return items_needing_stock


def create_material_request(doc):
	item_codes = [item.item_code for item in doc.items]
	items_needing_stock = get_shortage_by_sales_order_item(doc, get_projected_qty_map(item_codes))

	if not items_needing_stock:
		return

	try:
		mr = make_material_request(doc.name)
		mr.custom_is_amazon_pending = doc.amazon_order_status == "Pending"
		updated_items = []
		for mr_item in mr.items:
			if mr_item.sales_order_item in items_needing_stock:
				item_data = items_needing_stock[mr_item.sales_order_item]

				# Keep the Sales Order line's UOM. ERPNext compares item_code, uom and
				# conversion_factor against that line, so rewriting the row to "Nos" throws and
				# loses the whole request. required_qty is in stock units, so divide it back into
				# the line's own UOM and let stock_qty carry the real figure downstream.
				conversion_factor = flt(mr_item.conversion_factor) or 1
				mr_item.qty = item_data["required_qty"] / conversion_factor
				mr_item.stock_qty = item_data["required_qty"]
				mr_item.schedule_date = item_data["schedule_date"]

				updated_items.append(mr_item)

		mr.items = updated_items

		if not mr.items:
			return

		mr.material_request_type = "Purchase"

		mr.schedule_date = max([item.schedule_date for item in mr.items])
		mr.title = doc.customer_name if doc.customer_name else {doc.name}

		mr.insert()
		mr.submit()

		frappe.msgprint(
			_("Material Request {0} created for items with insufficient stock").format(
				frappe.utils.get_link_to_form("Material Request", mr.name)
			),
			alert=True,
			indicator="green",
		)

		return mr

	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title=f"Auto MR Creation Failed - SO {doc.name}")
		frappe.msgprint(
			_("Could not create Material Request automatically: {0}").format(str(e)),
			alert=True,
			indicator="red",
		)
