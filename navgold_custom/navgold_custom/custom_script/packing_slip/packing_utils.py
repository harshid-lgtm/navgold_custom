# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from erpnext.stock.utils import scan_barcode
from frappe import _
from frappe.utils import flt


@frappe.whitelist()
def resolve_barcode(search_value: str):
	if not search_value:
		return {"error": _("No barcode provided")}

	scan_result = scan_barcode(search_value)

	if not scan_result or not scan_result.get("item_code"):
		return {"error": _("Cannot find Item with this Barcode")}

	item_name = frappe.db.get_value("Item", scan_result.get("item_code"), "item_name")

	return {
		"item_code": scan_result.get("item_code"),
		"item_name": item_name or "",
		"barcode": scan_result.get("barcode"),
		"batch_no": scan_result.get("batch_no"),
		"serial_no": scan_result.get("serial_no"),
	}


def get_item_weight_map(item_codes: list):
	if not item_codes:
		return {}

	items = frappe.get_all(
		"Item",
		filters={"name": ["in", item_codes]},
		fields=["name", "weight_per_unit", "weight_uom"],
	)

	return {item.name: item for item in items}


def recalculate_net_weight(doc):
	item_codes = {item.item_code for item in doc.items}
	item_weights = get_item_weight_map(item_codes)

	for item in doc.items:
		weight_info = item_weights.get(item.item_code, {})
		weight_per_unit = flt(weight_info.get("weight_per_unit"))
		if weight_per_unit:
			item.net_weight = weight_per_unit * flt(item.qty)
			item.weight_uom = weight_info.get("weight_uom", "")
