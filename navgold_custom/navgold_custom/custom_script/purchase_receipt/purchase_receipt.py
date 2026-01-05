# Copyright (c) 2025, Navgold and contributors
# License: MIT License (see license.txt)

import json

import frappe
from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
from frappe import _
from frappe.utils import flt


@frappe.whitelist()
def scan_barcode_local(search_value):
	if not search_value:
		return None

	search_value = search_value.strip()

	barcode_data = frappe.db.get_value(
		"Item Barcode",
		{"barcode": search_value},
		["barcode", "parent as item_code", "uom"],
		as_dict=True,
	)

	if barcode_data:
		item_code = barcode_data.get("item_code")

		item_doc = frappe.get_cached_value("Item", item_code, "item_name")
		return {
			"barcode": barcode_data.get("barcode"),
			"item_code": item_code,
			"item_name": item_doc,
			"uom": barcode_data.get("uom"),
		}

	serial_data = frappe.db.get_value(
		"Serial No",
		search_value,
		["name as serial_no", "item_code", "batch_no"],
		as_dict=True,
	)

	if serial_data:
		item_code = serial_data.get("item_code")
		item_doc = frappe.get_cached_value("Item", item_code, "item_name")
		return {
			"serial_no": serial_data.get("serial_no"),
			"item_code": item_code,
			"item_name": item_doc,
			"batch_no": serial_data.get("batch_no"),
		}

	return None


@frappe.whitelist()
def get_open_pos_for_item(supplier, item_code):
	if not supplier or not item_code:
		frappe.throw(_("Supplier and Item Code are required"))

	po_names = frappe.db.get_all(
		"Purchase Order",
		filters={
			"supplier": supplier,
			"docstatus": 1,
			"status": ["not in", ["Closed", "On Hold"]],
			"per_received": ["<", 99.99],
		},
		pluck="name",
	)

	if not po_names:
		return []

	po_items = frappe.db.get_all(
		"Purchase Order Item",
		filters={
			"item_code": item_code,
			"parent": ["in", po_names],
		},
		fields=[
			"parent as po_name",
			"name as po_item_name",
			"item_code",
			"item_name",
			"qty",
			"received_qty",
			"uom",
			"rate",
		],
		order_by="parent desc, idx asc",
	)

	if not po_items:
		return []

	result = []
	processed_pos = set()

	for po_item in po_items:
		po_name = po_item.get("po_name")
		if po_name in processed_pos:
			continue
		processed_pos.add(po_name)

		ordered_qty = flt(po_item.get("qty", 0))
		received_qty = flt(po_item.get("received_qty", 0))
		pending_qty = ordered_qty - received_qty

		if pending_qty <= 0:
			continue

		po_doc = frappe.get_cached_doc("Purchase Order", po_name)

		result.append(
			{
				"po_name": po_name,
				"po_item_name": po_item.get("po_item_name"),
				"item_code": po_item.get("item_code"),
				"item_name": po_item.get("item_name"),
				"ordered_qty": ordered_qty,
				"received_qty": received_qty,
				"pending_qty": pending_qty,
				"uom": po_item.get("uom"),
				"rate": po_item.get("rate"),
				"supplier_name": po_doc.supplier_name,
				"creation": po_doc.creation,
			}
		)

	return result


def get_or_create_batch(batch_no, expiry_date, item_code):
	batch_exists = frappe.db.exists("Batch", batch_no)
	if batch_exists:
		frappe.db.set_value("Batch", batch_no, "expiry_date", expiry_date)
		return batch_no
	batch_doc = frappe.get_doc(
		{"doctype": "Batch", "batch_id": batch_no, "item": item_code, "expiry_date": expiry_date}
	).insert(ignore_permissions=True)
	return batch_doc.name


@frappe.whitelist()
def add_scanned_po_item_to_pr(po_name, po_item_name, qty, warehouse, batch_no, expiry_date, target_pr_doc):
	args = {"filtered_children": [po_item_name]}

	updated_doc = make_purchase_receipt(source_name=po_name, target_doc=target_pr_doc, args=json.dumps(args))

	for item in updated_doc.items:
		if item.purchase_order_item == po_item_name and item.purchase_order == po_name:
			item.qty = flt(qty)
			item.amount = (flt(item.qty)) * flt(item.rate)
			item.base_amount = (flt(item.qty)) * flt(item.rate) * flt(item.conversion_factor)
			item.stock_qty = flt(qty) * flt(item.conversion_factor)
			item.batch_no = get_or_create_batch(batch_no, expiry_date, item.item_code)
			if warehouse:
				item.warehouse = warehouse

			break
	# Filter out empty items
	updated_doc.items = [item for item in updated_doc.items if item.get("item_code")]
	return updated_doc
