# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice
from erpnext.stock.doctype.packed_item.packed_item import is_product_bundle
from frappe import _
from frappe.utils import flt

from navgold_custom.navgold_custom.custom_script.packing_slip.packing_utils import recalculate_net_weight


def before_validate(doc, method):
	recalculate_net_weight(doc)
	validate_packed_qty(doc)


def on_submit(doc, method):
	if not doc.delivery_note:
		return

	auto_submit_delivery_note(doc.delivery_note)


def auto_submit_delivery_note(delivery_note_name):
	delivery_note = frappe.get_cached_doc("Delivery Note", delivery_note_name)

	if delivery_note.docstatus != 0:
		return

	regular_items_packed = all(
		flt(item.packed_qty) >= flt(item.qty)
		for item in delivery_note.items
		if not is_product_bundle(item.item_code)
	)
	components_packed = all(flt(item.packed_qty) >= flt(item.qty) for item in delivery_note.packed_items)

	if not (regular_items_packed and components_packed):
		return

	delivery_note.submit()
	frappe.msgprint(
		_("Delivery Note {0} auto-submitted as all items are packed").format(delivery_note_name),
		indicator="green",
		alert=True,
	)

	auto_create_sales_invoice(delivery_note_name)


def auto_create_sales_invoice(delivery_note_name):
	try:
		sales_invoice = make_sales_invoice(delivery_note_name)
		sales_invoice.insert()
		sales_invoice.submit()
		frappe.msgprint(
			_("Sales Invoice {0} created and submitted automatically").format(sales_invoice.name),
			indicator="green",
			alert=True,
		)
	except Exception:
		frappe.log_error(title=_("Auto Sales Invoice Creation Failed for DN {0}").format(delivery_note_name))
		frappe.msgprint(
			_("Sales Invoice creation failed for Delivery Note {0}. Please create it manually.").format(
				delivery_note_name
			),
			indicator="orange",
			alert=True,
		)


def validate_packed_qty(doc):
	for item in doc.items:
		packed_qty = flt(item.custom_packed_qty)
		qty = flt(item.qty)
		if packed_qty != qty:
			frappe.throw(
				_("Row {0}: Packed Qty ({1}) does not match Required Qty ({2}) for item {3}").format(
					item.idx, packed_qty, qty, item.item_code
				)
			)
