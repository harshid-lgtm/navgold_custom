# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from erpnext.stock.doctype.delivery_note.delivery_note import make_packing_slip
from frappe import _
from frappe.utils import flt

from navgold_custom.navgold_custom.custom_script.packing_slip.packing_utils import (
	get_item_weight_map,
	recalculate_net_weight,
)


def before_validate(doc, method):
	recalculate_net_weight(doc)
	validate_packed_qty(doc)


def validate_packed_qty(doc):
	for item in doc.items:
		packed_qty = flt(item.custom_packed_qty)
		qty = flt(item.qty)
		if packed_qty != qty:
			frappe.throw(
				_("Row {0}: Packed Qty ({1}) does not match Required Qty ({2}) for item {3}").format(
					item.idx, packed_qty, qty, item.item_code
				)
			)


@frappe.whitelist()
def get_draft_delivery_note_items(tote: str | None = None):
	if not tote:
		return []

	pick_list_names = frappe.get_all(
		"Pick List",
		filters={"custom_tote": tote, "docstatus": 1, "status": "Open"},
		pluck="name",
	)

	if not pick_list_names:
		return []

	sales_orders = set(
		frappe.get_all(
			"Pick List Item",
			filters={"parent": ["in", pick_list_names]},
			pluck="sales_order",
		)
	)
	sales_orders.discard(None)

	if not sales_orders:
		return []

	delivery_note_items = frappe.get_all(
		"Delivery Note Item",
		filters={
			"docstatus": 0,
			"against_sales_order": ["in", sales_orders],
		},
		fields=["item_code", "item_name", "against_sales_order", "qty", "packed_qty", "parent"],
	)

	return [
		{
			"item_code": item.item_code,
			"item_name": item.item_name,
			"delivery_note": item.parent,
			"sales_order": item.against_sales_order,
		}
		for item in delivery_note_items
		if flt(item.packed_qty) < flt(item.qty)
	]


@frappe.whitelist()
def get_delivery_note_items(delivery_note: str):
	packing_slip_doc = make_packing_slip(delivery_note)
	item_codes = {item.item_code for item in packing_slip_doc.items}
	item_weights = get_item_weight_map(item_codes)

	return [
		{
			"item_code": item.item_code,
			"item_name": item.item_name,
			"description": item.description,
			"qty": item.qty,
			"uom": item.stock_uom,
			"dn_detail": item.dn_detail,
			"weight_per_unit": flt(item_weights.get(item.item_code, {}).get("weight_per_unit")),
			"weight_uom": item_weights.get(item.item_code, {}).get("weight_uom", ""),
		}
		for item in packing_slip_doc.items
	]
