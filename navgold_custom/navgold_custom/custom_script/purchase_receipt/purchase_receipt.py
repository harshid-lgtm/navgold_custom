# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import json

import frappe
from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
from frappe import _
from frappe.utils import flt


@frappe.whitelist()
def scan_barcode_local(search_value: str):
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
def get_open_pos_for_item(supplier: str, item_code: str, current_pr_items: str | None = None):
	if not supplier or not item_code:
		frappe.throw(_("Supplier and Item Code are required"))

	# Parse current PR items to account for qty already added
	qty_in_current_pr = {}
	if current_pr_items:
		if isinstance(current_pr_items, str):
			current_pr_items = frappe.parse_json(current_pr_items)
		for item in current_pr_items:
			if item.get("item_code") == item_code and item.get("purchase_order"):
				po_name = item.get("purchase_order")
				qty_in_current_pr[po_name] = qty_in_current_pr.get(po_name, 0) + flt(item.get("qty", 0))

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
	po_map = {}

	for row in po_items:
		po_name = row.po_name

		if po_name not in po_map:
			po_map[po_name] = {
				"po_item_name": row.po_item_name,
				"item_code": row.item_code,
				"item_name": row.item_name,
				"ordered_qty": 0,
				"received_qty": 0,
				"uom": row.uom,
				"rate": row.rate,
			}

		po_map[po_name]["ordered_qty"] += flt(row.qty)
		po_map[po_name]["received_qty"] += flt(row.received_qty)

	for po_name, data in po_map.items():
		# Subtract qty already in current PR from pending
		already_in_pr = flt(qty_in_current_pr.get(po_name, 0))
		pending_qty = data["ordered_qty"] - data["received_qty"] - already_in_pr

		if pending_qty <= 0:
			continue

		po_doc = frappe.get_cached_doc("Purchase Order", po_name)

		result.append(
			{
				"po_name": po_name,
				"po_item_name": data["po_item_name"],
				"item_code": data["item_code"],
				"item_name": data["item_name"],
				"ordered_qty": data["ordered_qty"],
				"received_qty": data["received_qty"],
				"pending_qty": pending_qty,
				"uom": data["uom"],
				"rate": data["rate"],
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
def add_scanned_po_item_to_pr(
	po_name: str,
	item_code: str,
	qty: str,
	warehouse: str,
	batch_no: str,
	expiry_date: str,
	target_pr_doc: str,
):
	qty = flt(qty)
	if qty <= 0:
		frappe.throw(_("Quantity must be greater than 0"))

	pr_doc_parsed = frappe.parse_json(target_pr_doc) if isinstance(target_pr_doc, str) else target_pr_doc

	# Build map of qty already in current PR for each PO line item
	qty_in_current_pr = {}
	if pr_doc_parsed and pr_doc_parsed.get("items"):
		for item in pr_doc_parsed.get("items"):
			if item.get("purchase_order") == po_name and item.get("item_code") == item_code:
				po_item_name = item.get("purchase_order_item")
				if po_item_name:
					qty_in_current_pr[po_item_name] = qty_in_current_pr.get(po_item_name, 0) + flt(
						item.get("qty", 0)
					)

	# Get all PO line items for this item, ordered by idx
	po_items = frappe.db.get_all(
		"Purchase Order Item",
		filters={
			"parent": po_name,
			"item_code": item_code,
		},
		fields=["name", "qty", "received_qty", "idx"],
		order_by="idx asc",
	)

	if not po_items:
		frappe.throw(_("No items found in Purchase Order {0} for item {1}").format(po_name, item_code))

	# Distribute qty across line items of PO
	distribution = []
	remaining_qty = qty

	for po_item in po_items:
		already_in_pr = flt(qty_in_current_pr.get(po_item.name, 0))
		pending = flt(po_item.qty) - flt(po_item.received_qty) - already_in_pr
		if pending <= 0:
			continue

		qty_to_receive = min(pending, remaining_qty)
		if qty_to_receive > 0:
			distribution.append(
				{
					"po_item_name": po_item.name,
					"qty": qty_to_receive,
				}
			)
			remaining_qty -= qty_to_receive

		if remaining_qty <= 0:
			break

	if not distribution:
		frappe.throw(_("No pending quantity to receive for item {0}").format(item_code))

	filtered_children = [d["po_item_name"] for d in distribution]
	args = {"filtered_children": filtered_children}

	updated_doc = make_purchase_receipt(source_name=po_name, target_doc=target_pr_doc, args=json.dumps(args))

	# Update quantities for each distributed item
	for dist in distribution:
		for item in updated_doc.items:
			if item.purchase_order_item == dist["po_item_name"] and item.purchase_order == po_name:
				item.qty = dist["qty"]
				item.amount = flt(item.qty) * flt(item.rate)
				item.base_amount = flt(item.qty) * flt(item.rate) * flt(item.conversion_factor)
				item.stock_qty = flt(item.qty) * flt(item.conversion_factor)
				item.batch_no = get_or_create_batch(batch_no, expiry_date, item.item_code)
				if warehouse:
					item.warehouse = warehouse
				break

	# Filter out empty items
	updated_doc.items = [item for item in updated_doc.items if item.get("item_code")]
	return updated_doc
