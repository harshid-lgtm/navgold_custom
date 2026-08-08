# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.data import add_to_date, flt, get_datetime

FBA_TRANSFER = "FBA Transfer"
TRANSFER_LINK_FIELD = "custom_fba_transfer_entry"


def update_proxy_sku_transfer_rates(doc, method):
	if doc.stock_entry_type != FBA_TRANSFER:
		return

	rate_by_item = {}
	for item_row in doc.items:
		if item_row.item_code in rate_by_item:
			continue
		rate_by_item[item_row.item_code] = flt(item_row.basic_rate)

	for item_code, rate in rate_by_item.items():
		frappe.db.set_value(
			"Proxy SKU",
			{"item": item_code, "is_fba": 1},
			"last_transfer_rate",
			rate,
			update_modified=False,
		)


def release_transferred_shelf_claim(doc, method):
	"""ERPNext settles a Pick List row's claim on the shelf off delivered_qty alone, and a
	transfer pick list never earns a Delivery Note — so a row whose stock has physically moved
	keeps claiming its shelf until every other row on the same pick list is transferred too.
	One straggler row silently freezes the stock behind every finished row beside it, and the
	shortfall only surfaces days later as "in stock, but nothing available to pick". Mirror the
	settled qty onto the field the claim reads so each row releases on its own."""
	if not doc.pick_list:
		return

	if frappe.db.get_value("Pick List", doc.pick_list, "purpose") == "Delivery":
		return

	# ponytail: overloads delivered_qty on transfer pick lists — nothing else reads it there,
	# since they never map a Delivery Note; move to a custom staged_qty field plus a
	# get_picked_items_details override if a transfer pick list ever needs to raise one
	for row in frappe.get_all(
		"Pick List Item",
		filters={"parent": doc.pick_list, "parenttype": "Pick List", "docstatus": 1},
		fields=["name", "delivered_qty", "transferred_qty"],
	):
		if flt(row.delivered_qty) == flt(row.transferred_qty):
			continue

		frappe.db.set_value(
			"Pick List Item",
			row.name,
			"delivered_qty",
			flt(row.transferred_qty),
			update_modified=False,
		)


def get_afn_material_issue(transfer_name):
	return frappe.db.get_value("Stock Entry", {TRANSFER_LINK_FIELD: transfer_name, "docstatus": ["<", 2]})


def create_afn_material_issue(doc, method):
	if doc.stock_entry_type != FBA_TRANSFER:
		return

	if get_afn_material_issue(doc.name):
		return

	afn_warehouse = frappe.db.get_single_value("Amazon SP API Settings", "afn_warehouse")
	if not afn_warehouse:
		frappe.throw(_("Set AFN Warehouse in Amazon SP API Settings before submitting an FBA Transfer."))

	transferred_rows = [row for row in doc.items if row.t_warehouse == afn_warehouse]
	if not transferred_rows:
		return

	material_issue = frappe.new_doc("Stock Entry")
	material_issue.stock_entry_type = "Material Issue"
	material_issue.company = doc.company
	material_issue.from_warehouse = afn_warehouse
	material_issue.set(TRANSFER_LINK_FIELD, doc.name)

	transferred_at = get_datetime(f"{doc.posting_date} {doc.posting_time}")
	posted_at = min(
		add_to_date(transferred_at, seconds=1),
		transferred_at.replace(hour=23, minute=59, second=59, microsecond=999999),
	)
	material_issue.set_posting_time = 1
	material_issue.posting_date = posted_at.date()
	material_issue.posting_time = posted_at.time()

	for row in transferred_rows:
		material_issue.append(
			"items",
			{
				"item_code": row.item_code,
				"qty": row.qty,
				"uom": row.uom,
				"stock_uom": row.stock_uom,
				"conversion_factor": row.conversion_factor,
				"s_warehouse": afn_warehouse,
				"use_serial_batch_fields": 1 if (row.batch_no or row.serial_no) else 0,
				"batch_no": row.batch_no,
				"serial_no": row.serial_no,
				"cost_center": row.cost_center,
			},
		)

	material_issue.insert(ignore_permissions=True)
	material_issue.submit()


def cancel_afn_material_issue(doc, method):
	if doc.stock_entry_type != FBA_TRANSFER:
		return

	issue_name = get_afn_material_issue(doc.name)
	if not issue_name:
		return

	material_issue = frappe.get_doc("Stock Entry", issue_name)
	material_issue.flags.ignore_permissions = True
	if material_issue.docstatus == 1:
		material_issue.cancel()
	else:
		material_issue.delete(ignore_permissions=True)
