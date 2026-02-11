# Copyright (c) 2025
# Navgold - MIT License

import frappe


@frappe.whitelist()
def get_draft_delivery_note_items(tote: str | None = None):
	"""Fetch items from draft Delivery Notes filtered by tote"""

	if not tote:
		return []

	pick_lists = frappe.get_all(
		"Pick List",
		filters={"custom_tote": tote, "docstatus": 1},
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
