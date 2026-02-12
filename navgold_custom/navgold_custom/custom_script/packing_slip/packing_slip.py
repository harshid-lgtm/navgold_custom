# Copyright (c) 2025, Navgold and contributors
# License: MIT License

import frappe


@frappe.whitelist()
def get_draft_delivery_note_items(tote: str | None = None):
	"""Fetch items from draft Delivery Notes filtered by tote"""
	if not tote:
		return []

	pick_lists = frappe.get_all(
		"Pick List",
		filters={"custom_tote": tote, "docstatus": 1, "status": "Open"},
		fields=["name"],
	)

	if not pick_lists:
		return []

	sales_orders = set()
	for pl in pick_lists:
		pl_items = frappe.get_all("Pick List Item", filters={"parent": pl.name}, fields=["sales_order"])
		for item in pl_items:
			if item.sales_order:
				sales_orders.add(item.sales_order)

	if not sales_orders:
		return []

	delivery_notes = frappe.get_all("Delivery Note", filters={"docstatus": 0}, fields=["name"])

	items_map = []
	for dn in delivery_notes:
		doc = frappe.get_cached_doc("Delivery Note", dn.name)

		for item in doc.items:
			if item.against_sales_order in sales_orders:
				items_map.append(
					{
						"item_code": item.item_code,
						"item_name": item.item_name,
						"delivery_note": doc.name,
						"sales_order": item.against_sales_order,
					}
				)

	return items_map


@frappe.whitelist()
def scan_barcode_and_get_delivery_note(search_value: str, tote: str | None = None):
	"""Scan barcode and fetch the relevant Delivery Note"""
	from erpnext.stock.utils import scan_barcode

	if not search_value:
		return {"error": "No barcode provided"}

	if not tote:
		return {"error": "Please scan or enter Tote first"}

	scan_result = scan_barcode(search_value)

	if not scan_result or not scan_result.get("item_code"):
		return {"error": "Cannot find Item with this Barcode"}

	item_code = scan_result.get("item_code")
	items = get_draft_delivery_note_items(tote)

	if not items:
		return {"error": f"No items found for tote {tote}"}

	for item in items:
		if item["item_code"] == item_code:
			return {
				"item_code": item_code,
				"item_name": item.get("item_name"),
				"delivery_note": item["delivery_note"],
				"sales_order": item.get("sales_order"),
				"barcode": scan_result.get("barcode"),
				"batch_no": scan_result.get("batch_no"),
				"serial_no": scan_result.get("serial_no"),
			}

	return {"error": f"Item {item_code} not found in any Delivery Note for tote {tote}"}
