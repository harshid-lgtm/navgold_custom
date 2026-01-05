# Attribution: Code adapted from eFeone's eSeller Suite (https://github.com/efeone/eseller_suite)
# Original work licensed under the MIT License
# Copyright (c) 2024, eFeone and contributors
#
# Modifications and additions
# Copyright (c) 2025, BWH Studios and contributors
# For license information, please see license.txt

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_material_request
from erpnext.stock.stock_ledger import get_stock_balance
from frappe import _
from frappe.utils import flt, getdate, nowdate


# Used for recalled orders, Amazon append's S at the start of ID
def sales_order_validate(doc, method):
	recall_order_prefixes = ["S"]
	if (
		doc.amazon_order_status != "Canceled"
		and not doc.amazon_order_amount
		and doc.amazon_order_id
		and doc.amazon_order_id[0] not in recall_order_prefixes
	):
		doc.amazon_order_amount = doc.total
	if doc.amazon_order_id and doc.amazon_order_id[0] in recall_order_prefixes:
		doc.amazon_order_amount = 0
	if doc.amazon_order_status == "Canceled" or doc.replaced_order_id:
		doc.amazon_order_amount = 0


def sales_order_on_update(doc, method):
	if doc.amazon_order_status == "Canceled" and doc.temporary_stock_tranfer_id:
		if frappe.db.exists("Stock Entry", {"name": doc.temporary_stock_tranfer_id, "docstatus": ["!=", 2]}):
			temp_stock_transfer_doc = frappe.get_doc("Stock Entry", doc.temporary_stock_tranfer_id)
			doc.temporary_stock_tranfer_id = ""
			doc.save(ignore_permissions=True)
			if temp_stock_transfer_doc.docstatus == 1:
				temp_stock_transfer_doc.cancel()
			temp_stock_transfer_doc.delete()


def sales_order_after_insert(doc, method):
	amz_setting = frappe.get_last_doc("Amazon SP API Settings", {"is_active": 1})
	if (
		amz_setting.temporary_stock_transfer_required
		and doc.amazon_order_id
		and doc.amazon_order_status != "Canceled"
	):
		# create_temporary_stock_transfer()
		pass


def create_temporary_stock_transfer(doc):
	"""method creates a stock entry to the temporary warehoue when a sales order is screated"""
	temp_stock_entry = frappe.new_doc("Stock Entry")
	temp_stock_entry.stock_entry_type = "Material Transfer"
	temp_stock_entry.set_posting_time = 1
	temp_stock_entry.posting_date = doc.transaction_date
	temp_stock_entry.posting_time = doc.transaction_time
	amz_setting = frappe.db.exists("Amazon SP API Settings", {"is_active": 1})
	warehouse = frappe.db.get_value("Amazon SP API Settings", amz_setting, "warehouse")
	if doc.fulfillment_channel:
		if doc.fulfillment_channel == "AFN":
			warehouse = frappe.db.get_value("Amazon SP API Settings", amz_setting, "afn_warehouse")
	for item in doc.items:
		temp_stock_entry.append(
			"items",
			{
				"s_warehouse": warehouse,
				"t_warehouse": frappe.db.get_value(
					"Amazon SP API Settings", amz_setting, "temporary_order_warehouse"
				),
				"item_code": item.item_code,
				"qty": item.qty,
				"allow_zero_valuation_rate": 1,
			},
		)
	temp_stock_entry.insert(ignore_permissions=True)
	doc.temporary_stock_tranfer_id = temp_stock_entry.name
	doc.save(ignore_permissions=True)
	frappe.db.savepoint("before_temp_stock_entry_submit")
	try:
		temp_stock_entry.submit()
	except Exception as e:
		frappe.db.rollback(save_point="before_temp_stock_entry_submit")
		failed_sync_record = frappe.new_doc("Amazon Failed Sync Record")
		failed_sync_record.amazon_order_id = doc.amazon_order_id
		failed_sync_record.remarks = "Failed to create temporary stock entry\n" + str(e)
		failed_sync_record.save(ignore_permissions=True)


def sales_order_after_delete(doc, method):
	"""method deletes the temporary stock entry if it exists"""
	if doc.temporary_stock_tranfer_id:
		if frappe.db.exists("Stock Entry", {"name": doc.temporary_stock_tranfer_id, "docstatus": ["!=", 2]}):
			temp_stock_transfer_doc = frappe.get_doc("Stock Entry", doc.temporary_stock_tranfer_id)
			if temp_stock_transfer_doc.docstatus == 1:
				temp_stock_transfer_doc.cancel()
			temp_stock_transfer_doc.delete()


# def sales_order_before_submit(doc, method):
# pass
# if doc.amazon_order_id:
# 	validate_amazon_so_submit(doc)


def validate_amazon_so_submit(doc):
	"""method validates the amazon sales order"""
	order_statuses = [
		"Shipped",
		"InvoiceUnconfirmed",
		"Unfulfillable",
	]

	order_status_valid = doc.amazon_order_status in order_statuses

	# Check if taxes are set for the order
	# If the order is a replacement order, taxes are already handled via jv
	has_taxes = len(doc.taxes) > 0 if not doc.replaced_order_id else True

	transfer_flag = True
	if doc.temporary_stock_tranfer_id:
		transfer_flag = frappe.db.exists(
			"Stock Entry", {"name": doc.temporary_stock_tranfer_id, "docstatus": 1}
		)

	if not (order_status_valid and has_taxes and transfer_flag):
		msg = f"Sales Order {doc.name} cannot be submitted."
		if not order_status_valid:
			msg += " Ensure that the order status is valid."
		if order_status_valid and not has_taxes and not doc.replaced_order_id:
			msg += " Ensure that taxes are set for the order."
		if order_status_valid and not transfer_flag:
			msg += " Ensure that the temporary stock transfer has been completed."
		frappe.throw(_(msg), title=_("Invalid Sales Order Submission"), exc=frappe.ValidationError)


def enq_si_submit(sales_invoice):
	insufficient_stock = False
	error_records = []

	# Collect stock levels for all items first (Assumption: Bulk fetching is possible)
	stock_levels = {
		item.item_code: get_stock_balance(item.item_code, item.warehouse, sales_invoice.posting_date)
		for item in sales_invoice.items
	}

	for item in sales_invoice.items:
		stock_qty = stock_levels.get(item.item_code, 0)
		if item.qty > stock_qty:
			insufficient_stock = True
			error_records.append(
				{
					"doctype": "Amazon Failed Invoice Record",
					"invoice_id": sales_invoice.name,
					"error": f"Insufficient stock for item {item.item_code} as of {sales_invoice.posting_date}. "
					f"Available: {stock_qty}, Required: {item.qty}",
				}
			)

	# Insert all error records in batch
	if error_records:
		for record in error_records:
			frappe.get_doc(record).insert()

	if not insufficient_stock:
		sales_invoice.flags.ignore_links = True
		sales_invoice.submit()


def sales_order_on_submit(doc, method):
	if doc.fulfillment_channel == "MFN":
		create_material_request(doc)
	else:
		doc.update_status("Closed")


def create_material_request(doc):
	items_needing_stock = {}

	for item in doc.items:
		projected_qty = flt(item.projected_qty)
		ordered_qty = flt(item.qty)

		if projected_qty < ordered_qty:
			if projected_qty >= 1:
				required_qty = ordered_qty - projected_qty
			else:
				required_qty = ordered_qty

			if required_qty <= 0:
				continue

			delivery_date = getdate(item.delivery_date) if item.delivery_date else getdate(doc.delivery_date)
			today = getdate(nowdate())

			schedule_date = delivery_date if delivery_date > today else today

			items_needing_stock[item.name] = {"required_qty": required_qty, "schedule_date": schedule_date}

	if not items_needing_stock:
		return

	try:
		mr = make_material_request(doc.name)
		mr.custom_is_amazon_pending = doc.amazon_order_status == "Pending"
		updated_items = []
		for mr_item in mr.items:
			if mr_item.sales_order_item in items_needing_stock:
				item_data = items_needing_stock[mr_item.sales_order_item]

				mr_item.qty = item_data["required_qty"]
				mr_item.stock_qty = flt(mr_item.qty) * flt(mr_item.conversion_factor)
				mr_item.amount = flt(mr_item.qty) * flt(mr_item.rate)
				mr_item.schedule_date = item_data["schedule_date"]

				updated_items.append(mr_item)

		mr.items = updated_items

		if not mr.items:
			return

		mr.material_request_type = "Purchase"

		mr.schedule_date = max([item.schedule_date for item in mr.items])

		mr.insert()
		mr.submit()

		frappe.msgprint(
			_("Material Request {0} created for items with insufficient stock").format(
				frappe.utils.get_link_to_form("Material Request", mr.name)
			),
			alert=True,
			indicator="green",
		)

		return mr

	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title=f"Auto MR Creation Failed - SO {doc.name}")
		frappe.msgprint(
			_("Could not create Material Request automatically: {0}").format(str(e)),
			alert=True,
			indicator="red",
		)
