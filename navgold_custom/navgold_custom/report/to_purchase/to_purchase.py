# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt
# MR: Material Request
# PO: Purchase Order
import math

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import flt
from frappe.utils.caching import redis_cache

material_request_status_list = ["Pending", "Partially Ordered", "Partially Received"]


def execute(filters: dict | None = None):
	"""Return columns and data for the report."""
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns() -> list[dict]:
	"""Return columns for the report."""
	return [
		{
			"label": _("Item Code"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 150,
		},
		{
			"label": _("Total Required Qty"),
			"fieldname": "total_required_qty",
			"fieldtype": "Float",
			"width": 150,
		},
		{
			"label": _("Supplier"),
			"fieldname": "supplier",
			"fieldtype": "Link",
			"options": "Supplier",
			"width": 200,
		},
		{
			"label": _("Supplier UOM"),
			"fieldname": "supplier_uom",
			"fieldtype": "Link",
			"options": "UOM",
			"width": 150,
		},
		{
			"label": _("Available Stock (UOM)"),
			"fieldname": "available_stock_uom",
			"fieldtype": "Float",
			"width": 150,
		},
		{
			"label": _("Available Stock (Normalized)"),
			"fieldname": "available_stock_normalized",
			"fieldtype": "Float",
			"width": 150,
		},
		{
			"label": _("Allocated Qty"),
			"fieldname": "allocated_qty",
			"fieldtype": "Float",
			"width": 150,
		},
		{
			"label": _("Remaining Qty"),
			"fieldname": "remaining_qty",
			"fieldtype": "Float",
			"width": 150,
		},
		{
			"label": _("Unit Price"),
			"fieldname": "unit_price",
			"fieldtype": "Currency",
			"width": 150,
		},
		{
			"label": _("UOM Price"),
			"fieldname": "uom_price",
			"fieldtype": "Currency",
			"width": 150,
		},
		{
			"label": _("Reference"),
			"fieldname": "ref_doc",
			"fieldtype": "Link",
			"options": "Supplier Item Price and Stock",
			"width": 150,
		},
	]


# Adds supplier to list until the required qty is satified
def get_data(filters):
	material_request = DocType("Material Request")
	material_request_item = DocType("Material Request Item")
	supplier_item_price_and_stock = DocType("Supplier Item Price and Stock")
	items_requests = (
		frappe.qb.from_(material_request)
		.left_join(material_request_item)
		.on(material_request_item.parent == material_request.name)
		.select(
			material_request_item.item_code,
			material_request_item.stock_qty.as_("required_qty"),
			material_request_item.ordered_qty,
		)
		.where(
			(material_request.material_request_type == "Purchase")
			& (material_request.status.isin(material_request_status_list))
		)
	).run(as_dict=True)

	items_needed = {}
	for r in items_requests:
		item = r.get("item_code")
		if not item:
			continue

		required = flt(r.get("required_qty") or 0)
		ordered = flt(r.get("ordered_qty") or 0)
		pending = max(0, required - ordered)

		if pending > 0:
			items_needed[item] = items_needed.get(item, 0) + pending

	supplier_list = (
		frappe.qb.from_(supplier_item_price_and_stock)
		.select(
			supplier_item_price_and_stock.item,
			supplier_item_price_and_stock.supplier,
			supplier_item_price_and_stock.unit_price,
			supplier_item_price_and_stock.price,
			supplier_item_price_and_stock.stock_qty,
			supplier_item_price_and_stock.normalized_qty,
			supplier_item_price_and_stock.uom,
			supplier_item_price_and_stock.name,
		)
		.where(
			(supplier_item_price_and_stock.purchase_blocked != 1)
			& (supplier_item_price_and_stock.stock_qty > 0)
			& (supplier_item_price_and_stock.item.isin(list(items_needed)))
		)
		.orderby(supplier_item_price_and_stock.item, supplier_item_price_and_stock.unit_price)
	).run(as_dict=True)

	results = []

	item_remaining = {item: qty for item, qty in items_needed.items()}

	for s in supplier_list:
		item_code = s["item"]

		remaining = item_remaining.get(item_code, 0)
		if remaining <= 0:
			continue

		total_required = items_needed[item_code]
		available_normalized = s["normalized_qty"]

		allocated = min(available_normalized, remaining)

		item_remaining[item_code] = remaining - allocated

		results.append(
			{
				"item_code": item_code,
				"total_required_qty": total_required,
				"supplier": s["supplier"],
				"supplier_uom": s["uom"],
				"available_stock_uom": s["stock_qty"],
				"available_stock_normalized": available_normalized,
				"allocated_qty": allocated,
				"remaining_qty": remaining - allocated,
				"unit_price": s["unit_price"],
				"uom_price": s["price"],
				"ref_doc": s["name"],
			}
		)

	return results


# This is a 3 step process
# 1. Based on supplier group items
# 2. Based on items get the pending MR
# 3. Based on each MR create a new line item in PO


@frappe.whitelist()
def create_purchase_orders_from_report(selected_items):
	"""Create Purchase Orders from selected report rows."""
	if isinstance(selected_items, str):
		selected_items = frappe.parse_json(selected_items)

	if not selected_items:
		frappe.throw(_("No items selected"))

	# Group items by supplier
	supplier_groups = {}
	for item in selected_items:
		supplier = item.get("supplier")
		if not supplier:
			continue

		if supplier not in supplier_groups:
			supplier_groups[supplier] = {"items": []}

		supplier_groups[supplier]["items"].append(item)

	created_pos = []

	for supplier, data in supplier_groups.items():
		try:
			po = create_purchase_order_with_mr_allocation(supplier, data)
			if po:
				created_pos.append(po.name)

		except Exception as e:
			frappe.log_error(f"Error creating PO for {supplier}: {e!s}")
			frappe.throw(_("Error creating Purchase Order for {0}: {1}").format(supplier, str(e)))

	return {
		"success": True,
		"purchase_orders": created_pos,
		"message": _("Created {0} Purchase Order(s)").format(len(created_pos)),
	}


def create_purchase_order_with_mr_allocation(supplier, data):
	"""Create PO and intelligently allocate to Material Requests."""

	items_to_order = [item.get("item_code") for item in data["items"]]

	material_request = DocType("Material Request")
	material_request_item = DocType("Material Request Item")

	mr_items = (
		frappe.qb.from_(material_request)
		.left_join(material_request_item)
		.on(material_request_item.parent == material_request.name)
		.select(
			material_request_item.item_code,
			material_request_item.stock_qty.as_("required_qty"),
			material_request_item.ordered_qty,
			material_request_item.name.as_("material_request_item"),
			material_request_item.parent.as_("material_request"),
			material_request_item.schedule_date,
		)
		.where(
			(material_request.material_request_type == "Purchase")
			& (material_request.status.isin(material_request_status_list))
			& (material_request_item.item_code.isin(items_to_order))
		)
		.orderby(material_request_item.schedule_date)
	).run(as_dict=True)

	amz_setting = frappe.get_last_doc("Amazon SP API Settings", {"is_active": 1})
	warehouse = amz_setting.warehouse
	mr_by_item = {}
	company = amz_setting.company

	# Based on items get the MR
	for mr in mr_items:
		item = mr["item_code"]
		pending = max(0, flt(mr["required_qty"]) - flt(mr["ordered_qty"]))

		if pending <= 0:
			continue

		if item not in mr_by_item:
			mr_by_item[item] = []

		mr_by_item[item].append(
			{
				"material_request": mr["material_request"],
				"material_request_item": mr["material_request_item"],
				"pending_qty": pending,
				"schedule_date": mr["schedule_date"],
			}
		)

	po = frappe.new_doc("Purchase Order")
	po.supplier = supplier
	po.company = company
	po.currency = frappe.db.get_default("currency")
	po.transaction_date = frappe.utils.today()

	earliest_date = None

	# Based on each item and MR create a line item though n^2 but not many
	for item_data in data["items"]:
		item_code = item_data.get("item_code")
		available_stock_uom = flt(item_data.get("available_stock_uom", 0))
		supplier_uom = item_data.get("supplier_uom")
		uom_price = flt(item_data.get("uom_price", 0))

		conversion_factor = get_conversion_factor(item_code, supplier_uom)

		mrs_for_item = mr_by_item.get(item_code, [])
		if not mrs_for_item:
			continue

		supplier_uom_remaining = available_stock_uom

		for mr in mrs_for_item:
			if supplier_uom_remaining <= 0:
				break

			mr_needs_normalized = mr["pending_qty"]

			# conversion to next ceiling since supplier may not sell in unit UOM
			mr_needs_uom = (
				math.ceil(mr_needs_normalized / conversion_factor)
				if conversion_factor > 0
				else mr_needs_normalized
			)

			qty_to_order = min(mr_needs_uom, supplier_uom_remaining)

			if qty_to_order <= 0:
				continue

			# Ensure schedule_date is always a string for consistent comparison
			schedule_date = mr["schedule_date"] or frappe.utils.today()
			if not isinstance(schedule_date, str):
				schedule_date = str(schedule_date)

			today_str = frappe.utils.today()

			if schedule_date < today_str:
				schedule_date = today_str

			if not earliest_date or schedule_date < earliest_date:
				earliest_date = schedule_date

			po.append(
				"items",
				{
					"item_code": item_code,
					"qty": qty_to_order,
					"uom": supplier_uom,
					"stock_uom": supplier_uom,
					"conversion_factor": conversion_factor,
					"rate": uom_price,
					"schedule_date": schedule_date,
					"warehouse": warehouse,
					"material_request": mr["material_request"],
					"material_request_item": mr["material_request_item"],
				},
			)
			supplier_uom_remaining -= qty_to_order
		update_supplier_stock_after_po(supplier, supplier_uom, item_code, supplier_uom_remaining)

	po.schedule_date = earliest_date or frappe.utils.today()

	if len(po.items) > 0:
		po.insert(ignore_permissions=True)
		po.submit()
		return po

	return None


@redis_cache()
def get_conversion_factor(item, supplier_uom):
	"""Fetch conversion factor from Item UOM table."""
	conv = frappe.db.get_value(
		"UOM Conversion Detail", {"parent": item, "uom": supplier_uom}, "conversion_factor"
	)
	return conv or 1


# Cannot use set_value doc_events 👀
def update_supplier_stock_after_po(supplier, supplier_uom, item_code, remaining_stock):
	supplier_item_price_and_stock = frappe.get_cached_doc(
		"Supplier Item Price and Stock", {"supplier": supplier, "item": item_code, "uom": supplier_uom}
	)
	supplier_item_price_and_stock.stock_qty = remaining_stock
	supplier_item_price_and_stock.save()
