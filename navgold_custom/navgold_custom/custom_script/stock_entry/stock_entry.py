# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime
from frappe.utils.data import flt

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


def create_afn_clearing_issue(doc, method=None):
	"""Issue out whatever an FBA Transfer just parked in the AFN warehouse.

	Amazon owns the stock from the moment it ships, so anything left sitting in the AFN Bin
	double-counts against our own inventory and skews the pricing strategy selector. Mirror
	the transfer with a Material Issue so the Bin nets back to zero.
	"""
	if doc.stock_entry_type != FBA_TRANSFER:
		return

	if frappe.db.exists("Stock Entry", {TRANSFER_LINK_FIELD: doc.name, "docstatus": ["<", 2]}):
		return

	afn_warehouse = frappe.db.get_single_value("Amazon SP API Settings", "afn_warehouse")
	if not afn_warehouse:
		frappe.throw(_("Set AFN Warehouse in Amazon SP API Settings before submitting an FBA Transfer."))

	issue = build_afn_clearing_issue(doc, afn_warehouse)
	if not issue:
		return

	issue.insert(ignore_permissions=True)
	issue.submit()


def cancel_afn_clearing_issue(doc, method=None):
	"""Drop the mirror Material Issue when its FBA Transfer is cancelled.

	The issue exists only to net off this transfer, so it cannot outlive it. This has to run on
	`before_cancel`: erpnext reverses the transfer's ledger entries inside its own `on_cancel`,
	and with the issue still holding the stock down that reversal fails on negative stock.
	"""
	if doc.stock_entry_type != FBA_TRANSFER:
		return

	issue_name = frappe.db.get_value("Stock Entry", {TRANSFER_LINK_FIELD: doc.name, "docstatus": 1})
	if issue_name:
		frappe.get_doc("Stock Entry", issue_name).cancel()


def build_afn_clearing_issue(transfer, afn_warehouse: str):
	"""Return an unsaved Material Issue mirroring the rows `transfer` moved into the AFN warehouse."""
	transferred_rows = [row for row in transfer.items if row.t_warehouse == afn_warehouse]
	if not transferred_rows:
		return None

	issue = frappe.new_doc("Stock Entry")
	issue.stock_entry_type = "Material Issue"
	issue.company = transfer.company
	issue.from_warehouse = afn_warehouse
	issue.set(TRANSFER_LINK_FIELD, transfer.name)

	# One second later keeps the issue strictly after the receipt in the stock ledger, so the
	# transfer never trips "negative stock" against its own incoming quantity. Keep the
	# microseconds erpnext stamps on posting_time — truncating them shortens the gap.
	posted_at = add_to_date(get_datetime(f"{transfer.posting_date} {transfer.posting_time}"), seconds=1)
	issue.set_posting_time = 1
	issue.posting_date = posted_at.date()
	issue.posting_time = posted_at.time()

	for row in transferred_rows:
		issue.append(
			"items",
			{
				"item_code": row.item_code,
				"qty": row.qty,
				"uom": row.uom,
				"stock_uom": row.stock_uom,
				"conversion_factor": row.conversion_factor,
				"s_warehouse": afn_warehouse,
				"batch_no": row.batch_no,
				"serial_no": row.serial_no,
				"cost_center": row.cost_center,
			},
		)

	return issue
