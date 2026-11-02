# Attribution: Code adapted from eFeone's eSeller Suite (https://github.com/efeone/eseller_suite)
# Original work licensed under the MIT License
# Copyright (c) 2024, eFeone and contributors
#
# Modifications and additions
# Copyright (c) 2025, BWH Studios and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.permissions import add_permission
from frappe.utils import flt

# A supplier ships whole packs, so a Purchase Order raised from To Purchase always rounds the
# Material Request's loose-unit demand up to the next pack. That rounding reads as an over-order,
# and with no allowance ERPNext rejects the whole batch. The overshoot is always under one pack but
# unbounded as a percentage — a single unit against a Pack of 12 supplier is already 1100% — so the
# allowance has to be large enough never to bind rather than tuned to a realistic overshoot.
OVER_ORDER_ALLOWANCE_PERCENT = 10000


def after_install():
	create_custom_fields(get_sales_order_custom_fields(), ignore_validate=True)
	create_custom_fields(get_sales_invoice_custom_fields(), ignore_validate=True)
	create_custom_fields(get_purchase_invoice_custom_fields(), ignore_validate=True)
	create_custom_fields(get_journal_entry_custom_fields(), ignore_validate=True)
	create_custom_fields(get_purchase_receipt_custom_fields(), ignore_validate=True)
	create_custom_fields(get_stock_entry_custom_fields(), ignore_validate=True)
	setup_navgold_role_permissions()


def set_over_order_allowance_for_pack_rounding():
	"""Returns the value it replaced, or None when the allowance was already high enough."""
	previous = flt(frappe.db.get_single_value("Buying Settings", "over_order_allowance"))
	if previous >= OVER_ORDER_ALLOWANCE_PERCENT:
		return None

	frappe.db.set_single_value("Buying Settings", "over_order_allowance", OVER_ORDER_ALLOWANCE_PERCENT)
	return previous


def setup_navgold_role_permissions():
	if frappe.db.exists("Role", "Navgold - Despatch Warehouse"):
		add_permission("Address", "Navgold - Despatch Warehouse", 0, "read")


def after_migrate():
	after_install()
	enable_prepared_report_for_strategy_price_comparison()
	ensure_pick_list_status_in_list_view()


def ensure_pick_list_status_in_list_view():
	"""Keep Pick List status visible after Frappe list-view layout changes."""
	property_setter = frappe.db.get_value(
		"Property Setter",
		{"doc_type": "Pick List", "field_name": "status", "property": "in_list_view"},
	)
	if property_setter:
		frappe.db.set_value("Property Setter", property_setter, "value", "1", update_modified=False)
	else:
		frappe.make_property_setter(
			{
				"doctype": "Pick List",
				"doctype_or_field": "DocField",
				"fieldname": "status",
				"property": "in_list_view",
				"value": "1",
				"property_type": "Check",
			},
			ignore_validate=True,
		)

	frappe.clear_cache(doctype="Pick List")


def enable_prepared_report_for_strategy_price_comparison():
	# This report downloads the Amazon listings report on every build, so it must run
	# as a Prepared (generated) report. prepared_report is a per-site runtime flag that
	# Frappe always exports as 0 in standard JSON (Report.before_export), so we enforce
	# it here on every migrate instead of in the report definition.
	report = "Amazon Strategy Price Comparison"
	if frappe.db.exists("Report", report):
		frappe.db.set_value("Report", report, "prepared_report", 1)


def before_uninstall():
	delete_custom_fields(get_sales_order_custom_fields())
	delete_custom_fields(get_sales_invoice_custom_fields())
	delete_custom_fields(get_purchase_invoice_custom_fields())
	delete_custom_fields(get_journal_entry_custom_fields())
	delete_custom_fields(get_purchase_receipt_custom_fields())


def delete_custom_fields(custom_fields: dict):
	"""
	Method to Delete custom fields
	args:
		custom_fields: a dict like `{'Sales Order': [{fieldname: 'amazon_order_id', ...}]}`
	"""
	for doctype, fields in custom_fields.items():
		frappe.db.delete(
			"Custom Field",
			{
				"fieldname": ("in", [field["fieldname"] for field in fields]),
				"dt": doctype,
			},
		)
		frappe.clear_cache(doctype=doctype)


def get_sales_order_custom_fields():
	"""
	Navgold Custom specific custom fields in Sales Order
	"""
	return {
		"Sales Order": [
			{
				"fieldname": "custom_amazon_details",
				"fieldtype": "Tab Break",
				"label": "Amazon Details",
				"insert_after": "connections_tab",
			},
			{
				"fieldname": "amazon_order_id",
				"fieldtype": "Data",
				"label": "Amazon Order ID",
				"insert_after": "custom_amazon_details",
				"in_standard_filter": 1,
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "amazon_order_status",
				"fieldtype": "Select",
				"label": "Amazon Order Status",
				"insert_after": "amazon_order_id",
				"options": "\nPendingAvailability\nPending\nUnshipped\nPartiallyShipped\nShipped\nInvoiceUnconfirmed\nCanceled\nUnfulfillable\nShipped - Picked Up",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "marketplace_id",
				"fieldtype": "Data",
				"label": "Marketplace ID",
				"insert_after": "amazon_order_status",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "amazon_customer_type",
				"fieldtype": "Select",
				"label": "Customer Type",
				"insert_after": "marketplace_id",
				"read_only": 1,
				"no_copy": 1,
				"options": "\nB2B\nB2C",
			},
			{"fieldname": "cb_1", "fieldtype": "Column Break", "insert_after": "amazon_customer_type"},
			{
				"fieldname": "fulfillment_channel",
				"fieldtype": "Select",
				"label": "Fulfillment Channel",
				"insert_after": "cb_1",
				"read_only": 1,
				"no_copy": 1,
				"options": "\nAFN\nMFN",
			},
			{
				"fieldname": "amazon_order_amount",
				"fieldtype": "Currency",
				"label": "Amazon Order Amount",
				"insert_after": "fulfillment_channel",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "is_prime",
				"fieldtype": "Check",
				"label": "Is Prime",
				"insert_after": "amazon_order_amount",
				"read_only": 1,
				"no_copy": 1,
				"in_standard_filter": 1,
			},
			{
				"fieldname": "dispatch_date",
				"fieldtype": "Date",
				"label": "Dispatch Date",
				"insert_after": "is_prime",
				"read_only": 1,
				"no_copy": 1,
				"in_standard_filter": 1,
				"description": "Amazon's LatestShipDate — SLA deadline to ship by",
			},
			{
				"fieldname": "shipping_address_line_1",
				"fieldtype": "Data",
				"label": "Shipping Address Line 1",
				"insert_after": "dispatch_date",
				"read_only": 1,
				"no_copy": 1,
				"in_standard_filter": 1,
			},
			{
				"fieldname": "shipping_city",
				"fieldtype": "Data",
				"label": "Shipping City",
				"insert_after": "shipping_address_line_1",
				"read_only": 1,
				"no_copy": 1,
				"in_standard_filter": 1,
			},
			{
				"fieldname": "shipping_postcode",
				"fieldtype": "Data",
				"label": "Shipping Postcode",
				"insert_after": "shipping_city",
				"read_only": 1,
				"no_copy": 1,
				"in_standard_filter": 1,
			},
		],
		"Sales Order Item": [
			{
				"fieldname": "total_order_value",
				"fieldtype": "Currency",
				"label": "Amazon Rate",
				"insert_after": "amount",
				"read_only": 1,
				"no_copy": 1,
				"in_list_view": 1,
			}
		],
	}


def get_sales_invoice_custom_fields():
	"""
	Navgold Custom specific custom fields in Sales Invoice
	"""
	return {
		"Sales Invoice": [
			{
				"fieldname": "amazon_order_id",
				"fieldtype": "Data",
				"label": "Amazon Order ID",
				"insert_after": "customer",
				"in_standard_filter": 1,
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "amazon_order_status",
				"fieldtype": "Select",
				"label": "Amazon Order Status",
				"insert_after": "amazon_order_id",
				"options": "\nPendingAvailability\nPending\nUnshipped\nPartiallyShipped\nShipped\nInvoiceUnconfirmed\nCanceled\nUnfulfillable\nShipped - Picked Up",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "amazon_customer_type",
				"fieldtype": "Select",
				"label": "Customer Type",
				"insert_after": "amazon_order_status",
				"read_only": 1,
				"no_copy": 1,
				"options": "\nB2B\nB2C",
			},
			{
				"fieldname": "fulfillment_channel",
				"fieldtype": "Select",
				"label": "Fulfillment Channel",
				"insert_after": "amazon_customer_type",
				"read_only": 1,
				"no_copy": 1,
				"options": "\nAFN\nMFN",
			},
			{
				"fieldname": "amazon_order_amount",
				"fieldtype": "Currency",
				"label": "Amazon Order Amount",
				"insert_after": "base_in_words",
				"read_only": 1,
				"no_copy": 1,
			},
		],
		"Sales Invoice Item": [
			{
				"fieldname": "amazon_details",
				"fieldtype": "Section Break",
				"label": "Amazon Details",
				"insert_after": "customer_item_code",
			},
			{
				"fieldname": "total_product_charges",
				"fieldtype": "Currency",
				"label": "Total Product Charges",
				"insert_after": "amazon_details",
			},
			{
				"fieldname": "total_promotional_rebates",
				"fieldtype": "Currency",
				"label": "Total Promotional Rebates",
				"insert_after": "total_product_charges",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "other",
				"fieldtype": "Currency",
				"label": "Other",
				"insert_after": "amazon_details",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "refunded",
				"fieldtype": "Check",
				"label": "Refunded",
				"insert_after": "other",
				"read_only": 1,
				"no_copy": 1,
			},
			{"fieldname": "cb_1", "fieldtype": "Column Break", "insert_after": "refunded"},
			{
				"fieldname": "amazon_fees",
				"fieldtype": "Currency",
				"label": "Amazon Fees",
				"insert_after": "cb_1",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "total_inr",
				"fieldtype": "Currency",
				"label": "Total INR",
				"insert_after": "amazon_fees",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "total_order_value",
				"fieldtype": "Currency",
				"label": "Amazon Rate",
				"insert_after": "total_inr",
				"read_only": 1,
				"no_copy": 1,
				"in_list_view": 1,
			},
		],
	}


def get_purchase_invoice_custom_fields():
	"""
	Navgold Custom specific custom fields in Purchase Invoice
	"""
	return {
		"Purchase Invoice": [
			{
				"fieldname": "flipkart_transaction_id",
				"fieldtype": "Data",
				"label": "Flipkart Transaction ID",
				"insert_after": "due_date",
				"in_standard_filter": 1,
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "flipkart_transaction_type",
				"fieldtype": "Data",
				"label": "Flipkart Transaction Type",
				"insert_after": "flipkart_transaction_id",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "wallet_redeem",
				"fieldtype": "Data",
				"label": "Wallet Redeem",
				"insert_after": "flipkart_transaction_type",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "wallet_topup",
				"fieldtype": "Data",
				"label": "Wallet Topup",
				"insert_after": "wallet_redeem",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "wallet_refund",
				"fieldtype": "Data",
				"label": "Wallet Refund",
				"insert_after": "wallet_topup",
				"read_only": 1,
				"no_copy": 1,
			},
		],
		"Purchase Invoice Item": [
			{
				"fieldname": "flipkart_payment_details",
				"fieldtype": "Section Break",
				"label": "Flipkart Payment Details",
				"insert_after": "customer_item_code",
			},
			{
				"fieldname": "wallet_redeem_reversal",
				"fieldtype": "Data",
				"label": "Wallet Redeem Reversal",
				"insert_after": "flipkart_payment_details",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "gst_on_ads_fees",
				"fieldtype": "Data",
				"label": "GST on Ads Fees",
				"insert_after": "wallet_redeem_reversal",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "settlement_value",
				"fieldtype": "Data",
				"label": "Settlement Value",
				"insert_after": "gst_on_ads_fees",
				"read_only": 1,
				"no_copy": 1,
			},
			{"fieldname": "cb_1", "fieldtype": "Column Break", "insert_after": "settlement_value"},
			{
				"fieldname": "wallet_redeem",
				"fieldtype": "Data",
				"label": "Wallet Redeem",
				"insert_after": "cb_1",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "wallet_topup",
				"fieldtype": "Data",
				"label": "Wallet Topup",
				"insert_after": "wallet_redeem",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "wallet_refund",
				"fieldtype": "Data",
				"label": "Wallet Refund",
				"insert_after": "wallet_topup",
				"read_only": 1,
				"no_copy": 1,
			},
		],
	}


def get_journal_entry_custom_fields():
	"""
	Navgold Custom specific custom fields in Journal Entry
	"""
	return {
		"Journal Entry": [
			{
				"fieldname": "amazon_order_id",
				"fieldtype": "Data",
				"label": "Amazon Order ID",
				"insert_after": "payment_order",
				"read_only": 1,
				"no_copy": 1,
				"description": "Reference for Replaced Order",
			}
		],
		"Journal Entry Account": [
			{
				"fieldname": "amazon_order_id",
				"fieldtype": "Data",
				"label": "Amazon Order ID",
				"insert_after": "reference_detail_no",
				"read_only": 1,
				"no_copy": 1,
			}
		],
	}


def get_purchase_receipt_custom_fields():
	"""Navgold Custom specific custom fields in Purchase Receipt"""
	return {
		"Purchase Receipt Item": [
			{
				"fieldname": "new_seral_section",
				"fieldtype": "Section Break",
				"label": "Serial Numbers",
				"insert_after": "batch_no",
				"hidden": 0,
			},
			{
				"fieldname": "barcode_no",
				"fieldtype": "Text",
				"label": "Serial Nos",
				"insert_after": "new_seral_section",
				"description": "Barcode numbers should be seprated by line break",
			},
		]
	}


def get_stock_entry_custom_fields():
	return {
		"Stock Entry Detail": [
			{
				"fieldname": "new_seral_section",
				"fieldtype": "Section Break",
				"label": "Serial Numbers",
				"insert_after": "batch_no",
				"hidden": 0,
			},
			{
				"fieldname": "barcode_no",
				"fieldtype": "Text",
				"label": "Serial Nos",
				"insert_after": "new_seral_section",
				"description": "Barcode numbers should be seprated by line break",
			},
		],
		"Stock Entry": [
			{
				"fieldname": "from_return_invoice",
				"fieldtype": "Check",
				"label": "From Return/Replaced Order",
				"insert_after": "sales_invoice_no",
				"read_only": 1,
			},
		],
	}
