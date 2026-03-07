# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt
from frappe.utils.caching import redis_cache

from navgold_custom.navgold_custom.custom_script.packing_slip.packing_utils import (
	get_item_weight_map,
	recalculate_net_weight,
)


class FBAPackingSlip(Document):
	def before_validate(self):
		self.adjust_qty_to_packed()
		recalculate_net_weight(self)
		self.validate_items()

	def adjust_qty_to_packed(self):
		for item in self.items:
			packed_qty = flt(item.custom_packed_qty)
			if packed_qty > 0:
				item.qty = packed_qty

	def validate_items(self):
		for item in self.items:
			if flt(item.custom_packed_qty) <= 0:
				frappe.throw(
					_(f"Row {item.idx}: Packed Qty must be greater than 0 for item {item.item_code}.")
				)


@frappe.whitelist()
def get_draft_stock_entry_items(tote: str | None = None):
	if not tote:
		return []

	pick_list_names = frappe.get_all(
		"Pick List",
		filters={"custom_tote": tote, "docstatus": 1, "purpose": "Material Transfer"},
		pluck="name",
	)

	if not pick_list_names:
		return []

	stock_entry_names = frappe.get_all(
		"Stock Entry",
		filters={
			"docstatus": 0,
			"stock_entry_type": "FBA Transfer",
			"pick_list": ["in", pick_list_names],
		},
		pluck="name",
	)

	if not stock_entry_names:
		return []

	stock_entry_items = frappe.get_all(
		"Stock Entry Detail",
		filters={"parent": ["in", stock_entry_names]},
		fields=["item_code", "item_name", "qty", "parent"],
	)

	return [
		{
			"item_code": item.item_code,
			"item_name": item.item_name,
			"stock_entry": item.parent,
		}
		for item in stock_entry_items
	]


@frappe.whitelist()
def get_stock_entry_items(stock_entry: str):
	rows = frappe.get_all(
		"Stock Entry Detail",
		filters={"parent": stock_entry},
		fields=["item_code", "item_name", "description", "transfer_qty", "stock_uom"],
	)

	item_codes = list({row.item_code for row in rows})
	item_weights = get_item_weight_map(item_codes)

	consolidated = {}
	for row in rows:
		key = row.item_code
		if key in consolidated:
			consolidated[key]["qty"] += flt(row.transfer_qty)
		else:
			weight_info = item_weights.get(key, {})
			consolidated[key] = {
				"item_code": row.item_code,
				"item_name": row.item_name,
				"description": row.description,
				"qty": flt(row.transfer_qty),
				"uom": row.stock_uom,
				"weight_per_unit": flt(weight_info.get("weight_per_unit")),
				"weight_uom": weight_info.get("weight_uom", ""),
			}

	already_packed = get_already_packed_qty(stock_entry)
	for item_code, packed_qty in already_packed.items():
		if item_code in consolidated:
			consolidated[item_code]["qty"] -= packed_qty

	return [item for item in consolidated.values() if flt(item["qty"]) > 0]


def get_already_packed_qty(stock_entry: str):
	packing_slips = frappe.get_all(
		"FBA Packing Slip",
		filters={
			"stock_entry": stock_entry,
			"docstatus": 1,
		},
		pluck="name",
	)

	if not packing_slips:
		return {}

	packed_items = frappe.get_all(
		"Packing Slip Item",
		filters={
			"parent": ["in", packing_slips],
			"parenttype": "FBA Packing Slip",
		},
		fields=["item_code", "qty"],
	)

	packed_map = {}
	for item in packed_items:
		packed_map[item.item_code] = flt(packed_map.get(item.item_code, 0)) + flt(item.qty)

	return packed_map


@frappe.whitelist()
@redis_cache(ttl=300)
def get_singles_conversion_factor(item_code: str):
	item_stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
	if item_stock_uom == "Nos":
		return 1.0

	nos_factor = frappe.db.get_value(
		"UOM Conversion Detail",
		{"parent": item_code, "parenttype": "Item", "uom": "Nos"},
		"conversion_factor",
	)

	if nos_factor:
		return 1.0 / flt(nos_factor)

	return 1.0
