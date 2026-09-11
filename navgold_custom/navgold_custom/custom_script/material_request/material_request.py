# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.query_builder.functions import Sum
from frappe.utils import flt, getdate, nowdate
from frappe.utils.file_lock import LockTimeoutError
from frappe.utils.synchronization import filelock

from navgold_custom.navgold_custom.utils import (
	get_allocatable_warehouse_filter,
	get_shelf_actual_qty_map,
)

OPEN_STATUSES = ("Pending", "Partially Ordered", "Partially Received")
SETTLED_SALES_ORDER_STATUSES = ("Closed", "Completed")

RECONCILE_LOCK = "navgold_reconcile_purchase_demand"

# Quantities come back through conversion factors, so a line can land a whisker either side of the
# figure it is compared against. Without a floor the dust alone is enough to stop a request and
# raise its replacement, forever.
QTY_TOLERANCE = 0.001


def retire_demand_for_sales_orders(sales_orders):
	if not sales_orders:
		return []

	return stop_material_requests_without_live_demand(get_open_material_requests(sales_orders))


def stop_material_requests_without_live_demand(material_requests):
	if not material_requests:
		return []

	rows = frappe.get_all(
		"Material Request Item",
		filters={"parent": ("in", material_requests), "docstatus": 1},
		fields=["parent", "sales_order_item"],
	)
	live_sales_order_items = get_live_sales_order_items(
		{row.sales_order_item for row in rows if row.sales_order_item}
	)

	still_wanted = {
		row.parent
		for row in rows
		if not row.sales_order_item or row.sales_order_item in live_sales_order_items
	}

	stopped = []
	for name in material_requests:
		if name in still_wanted:
			continue
		material_request = frappe.get_doc("Material Request", name)
		material_request.update_status("Stopped")
		stopped.append(name)

	return stopped


def get_live_sales_order_items(sales_order_items):
	if not sales_order_items:
		return set()

	rows = frappe.get_all(
		"Sales Order Item",
		filters={"name": ("in", list(sales_order_items))},
		fields=["name", "parent", "qty", "delivered_qty"],
	)
	if not rows:
		return set()

	sales_order_state = {
		row.name: row
		for row in frappe.get_all(
			"Sales Order",
			filters={"name": ("in", list({row.parent for row in rows}))},
			fields=["name", "docstatus", "status"],
		)
	}

	live = set()
	for row in rows:
		sales_order = sales_order_state.get(row.parent)
		if not sales_order or sales_order.docstatus == 2:
			continue
		if sales_order.status in SETTLED_SALES_ORDER_STATUSES:
			continue
		if flt(row.delivered_qty) >= flt(row.qty):
			continue
		live.add(row.name)

	return live


def get_open_material_requests(sales_orders=None):
	material_request = frappe.qb.DocType("Material Request")
	material_request_item = frappe.qb.DocType("Material Request Item")

	query = (
		frappe.qb.from_(material_request)
		.inner_join(material_request_item)
		.on(material_request.name == material_request_item.parent)
		.select(material_request.name)
		.distinct()
		.where(
			(material_request.material_request_type == "Purchase")
			& (material_request.docstatus == 1)
			& material_request.status.isin(OPEN_STATUSES)
		)
	)

	if sales_orders is not None:
		query = query.where(material_request_item.sales_order.isin(list(sales_orders)))

	return query.run(pluck=True)


def retire_settled_demand():
	stop_material_requests_without_live_demand(get_open_material_requests())


def retire_demand_on_delivery_note(doc, method=None):
	if doc.is_return:
		return

	sales_orders = {row.against_sales_order for row in doc.items if row.get("against_sales_order")}
	retire_demand_for_sales_orders(list(sales_orders))


def reconcile_purchase_demand() -> dict[str, list[str]]:
	from navgold_custom.navgold_custom.custom_script.sales_order.sales_order import (
		get_orders_open_for_supply,
	)

	sales_orders = get_orders_open_for_supply()
	if not sales_orders:
		return {"retired": [], "requested": []}

	# one allocation, spent by both halves. Sizing the two independently is what let a pass stop a
	# request and the very next one raise it again
	required_qty_map = get_required_qty_by_sales_order_item(sales_orders)

	retired = stop_requests_larger_than_demand(sales_orders, required_qty_map)
	# before the raises, or the replacement never gets written: erpnext's mapper keeps a line only
	# while qty - requested_qty - delivered_qty > 0 (sales_order/mapper.py:get_remaining_qty), and
	# the request just stopped is still counted in requested_qty. Again after, because submitting
	# the replacement recomputes that stale sum on top.
	sync_requested_qty(retired)
	requested = raise_requests_for_uncovered_demand(sales_orders, required_qty_map)
	sync_requested_qty(retired + requested)

	return {"retired": retired, "requested": requested}


def reconcile_purchase_demand_job() -> None:
	# the report reconciles on read and this runs hourly: unlocked, the two passes interleave and
	# each raises the request the other has just stopped
	try:
		with filelock(RECONCILE_LOCK, timeout=1):
			reconcile_purchase_demand()
	except LockTimeoutError:
		return


def get_required_qty_by_sales_order_item(sales_orders: list[str]) -> dict[str, float]:
	"""Per order line, the qty the shelf cannot cover.

	The one figure both halves of the reconciliation spend. Coverage is allocated earliest
	delivery date first, tie-broken on line name so the answer is stable between runs, and against
	``actual`` qty rather than free qty — the orders being walked *are* the reservations, so
	netting them twice would under-buy.
	"""
	item_codes = set(
		frappe.get_all(
			"Sales Order Item",
			filters={"parent": ("in", sales_orders), "docstatus": 1},
			pluck="item_code",
		)
	)
	if not item_codes:
		return {}

	order_lines = get_outstanding_order_lines(item_codes)
	if not order_lines:
		return {}

	shelf_qty_map = get_shelf_actual_qty_map(list(item_codes))

	required_qty_map = {}
	for row in sorted(order_lines, key=lambda line: (getdate(line.delivery_date), line.name)):
		outstanding = flt(row.stock_qty) - flt(row.delivered_qty)
		if outstanding <= 0:
			continue

		# an oversold bin reads negative; it covers nothing rather than owing the next line
		available = max(shelf_qty_map.get(row.item_code, 0), 0)
		covered = min(available, outstanding)
		shelf_qty_map[row.item_code] = available - covered
		required_qty_map[row.name] = outstanding - covered

	return required_qty_map


def stop_requests_larger_than_demand(
	sales_orders: list[str], required_qty_map: dict[str, float]
) -> list[str]:
	"""Stop every request asking for more than its line still needs.

	A request at or under the figure is left standing, which is what makes a second pass over an
	unchanged shelf do nothing at all. There is no way to shrink a submitted Material Request in
	place — qty is not allow_on_submit and update_child_qty_rate does not carry the doctype — so a
	line that has shrunk is stopped here and re-raised at the right size below.
	"""
	material_requests = get_open_material_requests(sales_orders)
	if not material_requests:
		return []

	retirable_items = get_retirable_material_request_items(material_requests, set(sales_orders))
	if not retirable_items:
		return []

	# measured against everything still asking for the line, not just the stoppable part: a line
	# carrying one request already on a Purchase Order and one clean request is over-asking by the
	# pair, and the clean one is the half we can still withdraw
	requested_by_order_line = get_requested_qty_by_order_line(
		get_submitted_material_request_items(material_requests)
	)
	overasking_order_lines = {
		sales_order_item
		for sales_order_item, requested_qty in requested_by_order_line.items()
		if requested_qty > flt(required_qty_map.get(sales_order_item, 0)) + QTY_TOLERANCE
	}
	if not overasking_order_lines:
		return []

	claimed_by = {}
	for row in retirable_items:
		claimed_by.setdefault(row.parent, []).append(row)

	retired = []
	for name, rows in claimed_by.items():
		if not any(row.sales_order_item in overasking_order_lines for row in rows):
			continue

		material_request = frappe.get_doc("Material Request", name)
		material_request.update_status("Stopped")
		retired.append(name)

	return retired


def raise_requests_for_uncovered_demand(
	sales_orders: list[str], required_qty_map: dict[str, float]
) -> list[str]:
	from navgold_custom.navgold_custom.custom_script.sales_order.sales_order import (
		raise_material_request,
	)

	# read back after the stops, so a request retired a moment ago no longer counts as cover.
	# Requests already on a Purchase Order are counted in full: that demand is bought, not shelved
	already_requested = get_requested_qty_by_order_line(
		get_submitted_material_request_items(get_open_material_requests(sales_orders))
	)

	today = getdate(nowdate())
	shortfall_by_order = {}
	order_lines = frappe.get_all(
		"Sales Order Item",
		filters={"parent": ("in", sales_orders), "docstatus": 1},
		fields=["name", "parent", "delivery_date"],
	)

	for row in order_lines:
		shortfall = flt(required_qty_map.get(row.name, 0)) - flt(already_requested.get(row.name, 0))
		if shortfall <= QTY_TOLERANCE:
			continue

		delivery_date = getdate(row.delivery_date) if row.delivery_date else today
		shortfall_by_order.setdefault(row.parent, {})[row.name] = {
			"required_qty": shortfall,
			"schedule_date": max(delivery_date, today),
		}

	requested = []
	for name, items_needing_stock in shortfall_by_order.items():
		material_request = raise_material_request(frappe.get_doc("Sales Order", name), items_needing_stock)
		if material_request:
			requested.append(material_request.name)

	return requested


def get_requested_qty_by_order_line(rows: list[frappe._dict]) -> dict[str, float]:
	requested_by_order_line = {}
	for row in rows:
		if not row.sales_order_item:
			continue
		requested_by_order_line[row.sales_order_item] = requested_by_order_line.get(
			row.sales_order_item, 0
		) + flt(row.stock_qty)
	return requested_by_order_line


def get_submitted_material_request_items(material_requests: list[str]) -> list[frappe._dict]:
	if not material_requests:
		return []

	return frappe.get_all(
		"Material Request Item",
		filters={"parent": ("in", material_requests), "docstatus": 1},
		fields=["parent", "name", "item_code", "stock_qty", "ordered_qty", "sales_order", "sales_order_item"],
	)


def get_retirable_material_request_items(
	material_requests: list[str], sales_orders: set[str]
) -> list[frappe._dict]:
	rows = get_submitted_material_request_items(material_requests)

	# erpnext guards every Material Request status rule with status != "Stopped", so one stopped
	# mid-purchase would never move again once the goods land
	held_back = {row.parent for row in rows if flt(row.ordered_qty) > 0}
	held_back |= {row.parent for row in rows if not row.sales_order_item}
	held_back |= {row.parent for row in rows if row.sales_order not in sales_orders}

	return [row for row in rows if row.parent not in held_back]


def get_outstanding_order_lines(item_codes: set[str]) -> list[frappe._dict]:
	# deliberately the same set erpnext holds in Bin.reserved_qty, picked and shipped orders
	# included: they still hold their stock, and crediting it elsewhere would oversell it. Gated on
	# the warehouses the shelf reading counts, or a line reserving at Amazon would eat cover our
	# own shelf never had.
	sales_order = frappe.qb.DocType("Sales Order")
	sales_order_item = frappe.qb.DocType("Sales Order Item")
	warehouse = frappe.qb.DocType("Warehouse")

	return (
		frappe.qb.from_(sales_order_item)
		.join(sales_order)
		.on(sales_order_item.parent == sales_order.name)
		.left_join(warehouse)
		.on(sales_order_item.warehouse == warehouse.name)
		.select(
			sales_order_item.name,
			sales_order_item.item_code,
			sales_order_item.stock_qty,
			sales_order_item.delivered_qty,
			sales_order_item.delivery_date,
		)
		.where(
			sales_order_item.item_code.isin(list(item_codes))
			& (sales_order.docstatus == 1)
			& sales_order.status.notin(("Closed", "Completed", "Cancelled"))
			& (sales_order_item.stock_qty > sales_order_item.delivered_qty)
			& get_allocatable_warehouse_filter(sales_order_item.warehouse)
		)
	).run(as_dict=True)


def sync_requested_qty(material_requests: list[str]) -> None:
	"""Re-point requested_qty at the requests that are still live.

	erpnext reads a stopped request two different ways. Bin.indented_qty excludes it
	(stock_balance.get_indented_qty filters status != "Stopped"), but the Sales Order line's
	requested_qty is summed by the status updater over every submitted Material Request Item with
	no status condition at all (status_updater._update_children). So a line that has been cut back
	still reads as fully requested, and the submit-time sizer — which caps each line at
	stock_qty - requested_qty — would never raise for it again.

	Run after the stops and the raises, or the next submit recomputes the stale sum on top.
	"""
	if not material_requests:
		return

	sales_order_items = set(
		frappe.get_all(
			"Material Request Item",
			filters={"parent": ("in", material_requests), "docstatus": 1},
			pluck="sales_order_item",
		)
	)
	sales_order_items.discard(None)
	if not sales_order_items:
		return

	material_request = frappe.qb.DocType("Material Request")
	material_request_item = frappe.qb.DocType("Material Request Item")

	rows = (
		frappe.qb.from_(material_request_item)
		.inner_join(material_request)
		.on(material_request_item.parent == material_request.name)
		.select(
			material_request_item.sales_order_item,
			Sum(material_request_item.stock_qty).as_("requested_qty"),
		)
		.where(
			material_request_item.sales_order_item.isin(list(sales_order_items))
			& (material_request.docstatus == 1)
			& (material_request.status != "Stopped")
		)
		.groupby(material_request_item.sales_order_item)
	).run(as_dict=True)

	live_requested_qty = {row.sales_order_item: flt(row.requested_qty) for row in rows}

	for sales_order_item in sales_order_items:
		frappe.db.set_value(
			"Sales Order Item",
			sales_order_item,
			"requested_qty",
			live_requested_qty.get(sales_order_item, 0),
			update_modified=False,
		)
