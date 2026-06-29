# Single source of truth for ERPNext cross-version imports.
#
# ERPNext v17 moved the `make_*` / `create_*` document-mapper functions out of the DocType
# controller modules into a dedicated `mapper.py` in each DocType package. We import from the
# new location and fall back to the old one so the app keeps working on v15/v16 and v17+.
# When we drop pre-v17 support, delete the fallbacks here — and nowhere else.

try:
	from erpnext.stock.doctype.delivery_note.mapper import make_packing_slip, make_sales_invoice
except ImportError:
	from erpnext.stock.doctype.delivery_note.delivery_note import make_packing_slip, make_sales_invoice

try:
	from erpnext.stock.doctype.pick_list.mapper import create_delivery_note, create_stock_entry
except ImportError:
	from erpnext.stock.doctype.pick_list.pick_list import create_delivery_note, create_stock_entry

try:
	from erpnext.selling.doctype.sales_order.mapper import create_pick_list, make_material_request
except ImportError:
	from erpnext.selling.doctype.sales_order.sales_order import create_pick_list, make_material_request

try:
	from erpnext.buying.doctype.purchase_order.mapper import make_purchase_receipt
except ImportError:
	from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

try: 
    from erpnext.stock.doctype.packed_item.packed_item import get_product_bundle_items
except ImportError:
    from erpnext.stock.doctype.packed_item.packed_item import get_product_bundle_items_by_name as get_product_bundle_items
    
__all__ = [
	"create_delivery_note",
	"create_pick_list",
	"create_stock_entry",
	"make_material_request",
	"make_packing_slip",
	"make_purchase_receipt",
	"make_sales_invoice",
	"get_product_bundle_items",
]
