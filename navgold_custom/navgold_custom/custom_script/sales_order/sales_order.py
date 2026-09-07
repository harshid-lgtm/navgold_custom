# Attribution: Code adapted from eFeone's eSeller Suite (https://github.com/efeone/eseller_suite)
# Original work licensed under the MIT License
# Copyright (c) 2024, eFeone and contributors
#
# Modifications and additions
# Copyright (c) 2025, BWH Studios and contributors
# For license information, please see license.txt

import frappe
from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
	get_sre_reserved_qty_for_items_and_warehouses as get_reserved_stock_details,
)
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import cint, flt, getdate, nowdate

from navgold_custom.navgold_custom.amazon_pricing.alerts import alert_material_request_failed
from navgold_custom.navgold_custom.erpnext_compat import make_material_request
from navgold_custom.navgold_custom.utils import get_free_qty_map


def sales_order_on_submit(doc, method):
	if doc.fulfillment_channel == "MFN":
		create_material_request(doc)
	elif doc.fulfillment_channel == "AFN":
		doc.update_status("Closed")


# Based on item_dashboard.py's get_data because we need to filter out rejected warehouses
def get_item_bin_data(item_code, warehouse=None):
	Bin = DocType("Bin")
	Warehouse = DocType("Warehouse")

	query = (
		frappe.qb.from_(Bin)
		.join(Warehouse)
		.on(Bin.warehouse == Warehouse.name)
		.select(
			Bin.item_code,
			Bin.warehouse,
			Bin.projected_qty,
			Bin.reserved_qty,
			Bin.reserved_qty_for_production,
			Bin.reserved_qty_for_sub_contract,
			Bin.actual_qty,
			Bin.valuation_rate,
		)
		.where(Warehouse.is_rejected_warehouse != 1)
	)

	query = query.where(Bin.item_code == item_code)

	if warehouse:
		query = query.where(Bin.warehouse == warehouse)

	allowed_wh = frappe.get_list("Warehouse", pluck="name", ignore_permissions=False)
	if allowed_wh:
		query = query.where(Bin.warehouse.isin(allowed_wh))
	query = query.where(
		(Bin.projected_qty != 0)
		| (Bin.reserved_qty != 0)
		| (Bin.reserved_qty_for_production != 0)
		| (Bin.reserved_qty_for_sub_contract != 0)
		| (Bin.actual_qty != 0)
	)

	items = query.run(as_dict=True)

	item_code_list = [item_code] if item_code else [i["item_code"] for i in items]
	warehouse_list = [i["warehouse"] for i in items]

	sre_reserved = get_reserved_stock_details(item_code_list, warehouse_list)
	precision = cint(frappe.db.get_single_value("System Settings", "float_precision"))
	for item in items:
		item.update(
			{
				"item_name": frappe.get_cached_value("Item", item["item_code"], "item_name"),
				"stock_uom": frappe.get_cached_value("Item", item["item_code"], "stock_uom"),
				"disable_quick_entry": (
					frappe.get_cached_value("Item", item["item_code"], "has_batch_no")
					or frappe.get_cached_value("Item", item["item_code"], "has_serial_no")
				),
				"projected_qty": flt(item["projected_qty"], precision),
				"reserved_qty": flt(item["reserved_qty"], precision),
				"reserved_qty_for_production": flt(item["reserved_qty_for_production"], precision),
				"reserved_qty_for_sub_contract": flt(item["reserved_qty_for_sub_contract"], precision),
				"actual_qty": flt(item["actual_qty"], precision),
				"reserved_stock": flt(sre_reserved.get((item["item_code"], item["warehouse"]))),
			}
		)

	return items


def get_shelf_qty_map(item_codes: list) -> dict:
	"""What this order can actually draw on: our own shelf, less what submitted orders reserve.

	Deliberately not ``Bin.projected_qty``. That folds in open Material Requests and Purchase
	Orders, which are real supply but claimed by nobody — so an order would be judged covered by
	paperwork raised for a different order, without any link recording it. When that paperwork was
	later stopped, the order it was silently covering was stranded with nothing on order and no
	alert. Every order now asks only what is on the shelf and raises its own request.

	Stock held at Amazon needs no special case: the AFN warehouse is flagged as a rejected
	warehouse, which this reading already excludes.

	Draft Amazon orders are deliberately not netted off: a draft reserves nothing, and on submit it
	either reserves the stock or raises its own request. What this reading cannot do is revisit an
	order once its request is written, so a shelf unit counted here and later consumed by another
	order leaves the earlier order short. request_stock_for_uncovered_orders is what settles that,
	after the loss is real rather than predicted.
	"""
	free_qty_map = get_free_qty_map(item_codes)
	return {item_code: flt(row.get("free_qty")) for item_code, row in free_qty_map.items()}


def get_shortage_by_sales_order_item(doc, shelf_qty_map):
	shortage_by_item = {
		item_code: abs(free_qty) for item_code, free_qty in shelf_qty_map.items() if free_qty < 0
	}

	today = getdate(nowdate())
	items_needing_stock = {}

	for item in doc.items:
		remaining_shortage = shortage_by_item.get(item.item_code, 0)
		if remaining_shortage <= 0:
			continue

		# The shortage is item-wide, but the Material Request line hangs off this one Sales Order
		# line and ERPNext validates it against that line's stock_qty. Asking for the whole
		# item-wide figure throws OverAllowanceError and loses the entire request, so each line
		# takes only its own share and the rest stays with the orders that caused it.
		required_qty = min(remaining_shortage, flt(item.stock_qty) - flt(item.requested_qty))
		if required_qty <= 0:
			continue

		shortage_by_item[item.item_code] = remaining_shortage - required_qty

		delivery_date = getdate(item.delivery_date) if item.delivery_date else getdate(doc.delivery_date)
		items_needing_stock[item.name] = {
			"required_qty": required_qty,
			"schedule_date": delivery_date if delivery_date > today else today,
		}

	return items_needing_stock


def create_material_request(doc):
	item_codes = [item.item_code for item in doc.items]
	items_needing_stock = get_shortage_by_sales_order_item(doc, get_shelf_qty_map(item_codes))

	if not items_needing_stock:
		return

	try:
		mr = make_material_request(doc.name)
		mr.custom_is_amazon_pending = doc.amazon_order_status == "Pending"
		updated_items = []
		for mr_item in mr.items:
			if mr_item.sales_order_item in items_needing_stock:
				item_data = items_needing_stock[mr_item.sales_order_item]

				# Keep the Sales Order line's UOM. ERPNext compares item_code, uom and
				# conversion_factor against that line, so rewriting the row to "Nos" throws and
				# loses the whole request. required_qty is in stock units, so divide it back into
				# the line's own UOM and let stock_qty carry the real figure downstream.
				conversion_factor = flt(mr_item.conversion_factor) or 1
				mr_item.qty = item_data["required_qty"] / conversion_factor
				mr_item.stock_qty = item_data["required_qty"]
				mr_item.schedule_date = item_data["schedule_date"]

				updated_items.append(mr_item)

		mr.items = updated_items

		if not mr.items:
			return

		mr.material_request_type = "Purchase"

		mr.schedule_date = max([item.schedule_date for item in mr.items])
		mr.title = doc.customer_name if doc.customer_name else {doc.name}

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

	except Exception as error:
		# Swallowed on purpose: the order sync must not lose an Amazon order because its request
		# failed. But the msgprint lands nowhere in a background job and the Error Log is purged
		# after 14 days, so the shortage would go missing in silence — alert instead of trusting it.
		frappe.log_error(message=frappe.get_traceback(), title=f"Auto MR Creation Failed - SO {doc.name}")
		alert_material_request_failed(doc.name, str(error))
		frappe.msgprint(
			_("Could not create Material Request automatically: {0}").format(str(error)),
			alert=True,
			indicator="red",
		)


# Amazon has already handed these to the carrier, whatever ERPNext's delivery state says — several
# carry per_delivered = 0 because the Delivery Note was never made. Requesting stock for them would
# buy replacements for goods the customer already has.
SHIPPED_ORDER_STATUSES = ("Shipped", "PartiallyShipped")


def request_stock_for_uncovered_orders() -> list[str]:
	"""Top every open merchant-fulfilled order back up to its own line.

	Idempotent by construction: erpnext raises the Sales Order line's requested_qty when a request
	is submitted, and the per-line cap is stock_qty minus that, so an order already covered asks for
	nothing. Safe to run on every migrate, and safe to run twice.
	"""
	# imported inside the function: amazon_repository pulls in the SP-API client at module level,
	# and this module is loaded on every Sales Order save
	from navgold_custom.navgold_custom.amazon_repository import CLOSED_ORDER_STATUSES

	sales_orders = frappe.get_all(
		"Sales Order",
		filters={
			"docstatus": 1,
			"fulfillment_channel": "MFN",
			"status": ("not in", ("Closed", "Completed", "Cancelled")),
			"amazon_order_status": ("not in", SHIPPED_ORDER_STATUSES + CLOSED_ORDER_STATUSES),
		},
		pluck="name",
	)
	if not sales_orders:
		return []

	candidates = get_orders_short_of_stock(sales_orders)

	requested = []
	for name in candidates:
		# create_material_request needs the whole document, so this is one read per candidate — which
		# is why the shelf screen above runs first rather than walking every open order.
		material_request = create_material_request(frappe.get_doc("Sales Order", name))
		if material_request:
			requested.append(material_request.name)

	return requested


def get_orders_short_of_stock(sales_orders: list[str]) -> list[str]:
	"""Orders holding a line for an item whose shelf is already in deficit, still short of its own
	request. A deliberately generous screen: an item with stock to spare would raise nothing anyway,
	so this only skips the reads that create_material_request would have thrown away."""
	order_items = frappe.get_all(
		"Sales Order Item",
		filters={"parent": ("in", sales_orders), "docstatus": 1},
		fields=["parent", "item_code", "stock_qty", "requested_qty"],
	)
	if not order_items:
		return []

	shelf_qty_map = get_shelf_qty_map(list({row.item_code for row in order_items}))

	candidates = {
		row.parent
		for row in order_items
		if shelf_qty_map.get(row.item_code, 0) < 0 and flt(row.stock_qty) > flt(row.requested_qty)
	}
	return [name for name in sales_orders if name in candidates]
