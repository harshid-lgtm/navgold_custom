# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder import DocType, Order
from frappe.utils import flt


def execute(filters=None):
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{
			"label": _("Material Request"),
			"fieldname": "material_request",
			"fieldtype": "Link",
			"options": "Material Request",
			"width": 180,
		},
		{"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 100},
		{
			"label": _("Item"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 160,
		},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 220},
		{
			"label": _("Target Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 180,
		},
		{
			"label": _("Qty to Transfer"),
			"fieldname": "qty_to_transfer",
			"fieldtype": "Float",
			"width": 130,
		},
		{
			"label": _("MR UOM"),
			"fieldname": "mr_uom",
			"fieldtype": "Link",
			"options": "UOM",
			"width": 100,
		},
		{
			"label": _("Purchase Order"),
			"fieldname": "purchase_order",
			"fieldtype": "Link",
			"options": "Purchase Order",
			"width": 180,
		},
		{
			"label": _("Ordered Qty"),
			"fieldname": "ordered_qty",
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"label": _("PO UOM"),
			"fieldname": "po_uom",
			"fieldtype": "Link",
			"options": "UOM",
			"width": 100,
		},
		{
			"label": _("Received Qty"),
			"fieldname": "received_qty",
			"fieldtype": "Float",
			"width": 120,
		},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 120},
	]


def get_data(filters):
	material_request = DocType("Material Request")
	material_request_item = DocType("Material Request Item")
	purchase_order = DocType("Purchase Order")
	purchase_order_item = DocType("Purchase Order Item")
	item = DocType("Item")

	mr_rows = (
		frappe.qb.from_(material_request)
		.join(material_request_item)
		.on(material_request_item.parent == material_request.name)
		.join(item)
		.on(item.name == material_request_item.item_code)
		.select(
			material_request_item.name.as_("mr_item_name"),
			material_request.name.as_("material_request"),
			material_request.transaction_date.as_("date"),
			material_request_item.item_code,
			item.item_name,
			material_request_item.warehouse,
			material_request_item.qty.as_("qty_to_transfer"),
			material_request_item.uom.as_("mr_uom"),
			material_request_item.custom_purchase_order,
		)
		.where(
			(material_request.material_request_type == "Material Transfer")
			& (material_request.docstatus == 1)
			& (material_request.status == "Pending")
		)
		.orderby(material_request.transaction_date, order=Order.desc)
	).run(as_dict=True)

	if not mr_rows:
		return []

	# Collect (po_name, item_code) pairs that need PO item lookup
	po_item_pairs = []
	for row in mr_rows:
		po_name = row.get("custom_purchase_order")
		if po_name:
			po_item_pairs.append((po_name, row["item_code"]))

	po_item_data = {}
	if po_item_pairs:
		po_names = list({pair[0] for pair in po_item_pairs})
		item_codes = list({pair[1] for pair in po_item_pairs})

		po_item_rows = (
			frappe.qb.from_(purchase_order)
			.join(purchase_order_item)
			.on(purchase_order_item.parent == purchase_order.name)
			.select(
				purchase_order.name.as_("purchase_order"),
				purchase_order_item.item_code,
				purchase_order_item.qty.as_("ordered_qty"),
				purchase_order_item.received_qty,
				purchase_order_item.uom.as_("po_uom"),
			)
			.where(
				(purchase_order.docstatus != 2)
				& (purchase_order.name.isin(po_names))
				& (purchase_order_item.item_code.isin(item_codes))
			)
		).run(as_dict=True)

		for po_row in po_item_rows:
			key = (po_row["purchase_order"], po_row["item_code"])
			po_item_data[key] = {
				"ordered_qty": flt(po_row["ordered_qty"]),
				"received_qty": flt(po_row["received_qty"]),
				"po_uom": po_row["po_uom"],
			}

	# Batch-fetch conversion factors for all unique (item, uom) pairs
	uom_conversion_detail = DocType("UOM Conversion Detail")
	all_item_uom_pairs = set()
	for row in mr_rows:
		all_item_uom_pairs.add((row["item_code"], row["mr_uom"]))
		po_name = row.get("custom_purchase_order") or ""
		po_data = po_item_data.get((po_name, row["item_code"]), {})
		if po_data.get("po_uom"):
			all_item_uom_pairs.add((row["item_code"], po_data["po_uom"]))

	conversion_factor_map = {}
	if all_item_uom_pairs:
		all_item_codes = list({pair[0] for pair in all_item_uom_pairs})
		all_uoms = list({pair[1] for pair in all_item_uom_pairs})
		conversion_rows = (
			frappe.qb.from_(uom_conversion_detail)
			.select(
				uom_conversion_detail.parent,
				uom_conversion_detail.uom,
				uom_conversion_detail.conversion_factor,
			)
			.where(
				(uom_conversion_detail.parent.isin(all_item_codes))
				& (uom_conversion_detail.uom.isin(all_uoms))
			)
		).run(as_dict=True)

		for conv_row in conversion_rows:
			conversion_factor_map[(conv_row["parent"], conv_row["uom"])] = (
				flt(conv_row["conversion_factor"]) or 1.0
			)

	data = []
	for row in mr_rows:
		po_name = row.get("custom_purchase_order") or ""
		po_data = po_item_data.get((po_name, row["item_code"]), {}) if po_name else {}

		item_code = row["item_code"]
		mr_uom = row["mr_uom"]
		po_uom = po_data.get("po_uom")

		mr_conversion_factor = conversion_factor_map.get((item_code, mr_uom), 1.0)
		po_conversion_factor = conversion_factor_map.get((item_code, po_uom), 1.0) if po_uom else 1.0

		qty_to_transfer = flt(row["qty_to_transfer"])
		ordered_qty = flt(po_data.get("ordered_qty", 0))
		received_qty = flt(po_data.get("received_qty", 0))

		qty_to_transfer_base = qty_to_transfer * mr_conversion_factor
		received_qty_base = received_qty * po_conversion_factor

		if received_qty_base >= qty_to_transfer_base and qty_to_transfer_base > 0:
			status = "Received"
		elif received_qty_base > 0:
			status = "Partially Received"
		elif ordered_qty > 0:
			status = "Ordered"
		else:
			status = "Pending PO"

		data.append(
			{
				"material_request": row["material_request"],
				"date": row["date"],
				"item_code": row["item_code"],
				"item_name": row["item_name"],
				"warehouse": row["warehouse"],
				"qty_to_transfer": qty_to_transfer,
				"mr_uom": row["mr_uom"],
				"purchase_order": po_name or None,
				"ordered_qty": ordered_qty,
				"po_uom": po_data.get("po_uom"),
				"received_qty": received_qty,
				"status": status,
			}
		)

	return data
