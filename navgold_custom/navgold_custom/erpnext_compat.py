# Single source of truth for ERPNext cross-version imports.
#
# ERPNext v17 moved the `make_*` / `create_*` document-mapper functions out of the DocType
# controller modules into a dedicated `mapper.py` in each DocType package. We import from the
# new location and fall back to the old one so the app keeps working on v15/v16 and v17+.
# When we drop pre-v17 support, delete the fallbacks here — and nowhere else.

import inspect

import frappe
from erpnext.stock.doctype.stock_reservation_entry import stock_reservation_entry as sre_module

try:
	from erpnext.stock.doctype.delivery_note.mapper import make_packing_slip, make_sales_invoice
except ImportError:
	from erpnext.stock.doctype.delivery_note.delivery_note import make_packing_slip, make_sales_invoice

try:
	from erpnext.stock.doctype.pick_list.mapper import (
		create_dn_with_so,
		create_dn_wo_so,
		create_stock_entry,
		validate_item_locations,
	)
except ImportError:
	from erpnext.stock.doctype.pick_list.pick_list import (
		create_dn_with_so,
		create_dn_wo_so,
		create_stock_entry,
		validate_item_locations,
	)

try:
	from erpnext.selling.doctype.sales_order.mapper import (
		create_pick_list,
		make_delivery_note,
		make_material_request,
	)
except ImportError:
	from erpnext.selling.doctype.sales_order.sales_order import (
		create_pick_list,
		make_delivery_note,
		make_material_request,
	)

try:
	from erpnext.buying.doctype.purchase_order.mapper import make_purchase_receipt
except ImportError:
	from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

try:
	from erpnext.stock.doctype.material_request.mapper import make_purchase_order
except ImportError:
	from erpnext.stock.doctype.material_request.material_request import make_purchase_order

try:
	from erpnext.stock.doctype.packed_item.packed_item import get_product_bundle_items
except ImportError:
	# v17+: Product Bundle is versioned, no longer named by item_code; resolve active version first.
	from erpnext.selling.doctype.product_bundle.product_bundle import get_active_product_bundle
	from erpnext.stock.doctype.packed_item.packed_item import get_product_bundle_items_by_name

	def get_product_bundle_items(item_code):
		bundle_name = get_active_product_bundle(item_code)
		if not bundle_name:
			return []
		return get_product_bundle_items_by_name(bundle_name)


def get_reserved_batch_bundle_name(sre_list):
	if not sre_list:
		return None

	if "sre_list" in inspect.signature(sre_module.get_ssb_bundle_for_voucher).parameters:
		bundle = sre_module.get_ssb_bundle_for_voucher(sre_list)
		return bundle.name if bundle else None

	bundle_name = sre_module.get_ssb_bundle_for_voucher(sre_list[0])
	if not bundle_name or len(sre_list) == 1:
		return bundle_name

	bundle = frappe.get_doc("Serial and Batch Bundle", bundle_name)
	for sre in sre_list[1:]:
		for sb_entry in sre_module.get_serial_batch_entries_for_voucher(sre["name"]):
			bundle.append("entries", sb_entry)
	bundle.save()

	return bundle.name


__all__ = [
	"create_dn_with_so",
	"create_dn_wo_so",
	"create_pick_list",
	"create_stock_entry",
	"get_product_bundle_items",
	"get_reserved_batch_bundle_name",
	"make_delivery_note",
	"make_material_request",
	"make_packing_slip",
	"make_purchase_receipt",
	"make_sales_invoice",
	"validate_item_locations",
]
