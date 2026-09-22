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
		return {"retired": [], "requested": [], "blocked": []}

	order_lines = frappe.get_all(
		"Sales Order Item",
		filters={"parent": ("in", sales_orders), "docstatus": 1},
		pluck="name",
	)
	required_qty_map = get_required_qty_by_sales_order_item(sales_orders)

	previously_muted = frappe.flags.mute_messages
	frappe.flags.mute_messages = True
	try:
		retired = stop_requests_larger_than_demand(sales_orders, required_qty_map)
		sync_requested_qty(order_lines)
		requested, blocked = raise_requests_for_uncovered_demand(sales_orders, required_qty_map)
		sync_requested_qty(order_lines)
	finally:
		frappe.flags.mute_messages = previously_muted

	report_blocked_orders(blocked)

	return {"retired": retired, "requested": requested, "blocked": blocked}


def report_blocked_orders(blocked: list[str]) -> None:
	if not blocked:
		return

	# one entry for the pass, not one per order: a refusal repeats on every run until someone fixes
	# the order behind it
	frappe.log_error(
		title="Purchase demand could not be raised",
		message="Material Request refused for: " + ", ".join(sorted(blocked)),
	)


def reconcile_purchase_demand_job() -> dict[str, list[str]] | None:
	try:
		with filelock(RECONCILE_LOCK, timeout=1):
			return reconcile_purchase_demand()
	except LockTimeoutError:
		return None


def get_required_qty_by_sales_order_item(sales_orders: list[str]) -> dict[str, float]:
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

		available = max(shelf_qty_map.get(row.item_code, 0), 0)
		covered = min(available, outstanding)
		shelf_qty_map[row.item_code] = available - covered
		required_qty_map[row.name] = outstanding - covered

	return required_qty_map


def stop_requests_larger_than_demand(
	sales_orders: list[str], required_qty_map: dict[str, float]
) -> list[str]:
	material_requests = get_open_material_requests(sales_orders)
	if not material_requests:
		return []

	retirable_items = get_retirable_material_request_items(material_requests, set(sales_orders))
	if not retirable_items:
		return []

	supply_by_order_line = get_supply_by_order_line(
		list({row.sales_order_item for row in retirable_items if row.sales_order_item})
	)
	overasking_order_lines = {
		sales_order_item
		for sales_order_item, supply_qty in supply_by_order_line.items()
		if supply_qty > flt(required_qty_map.get(sales_order_item, 0)) + QTY_TOLERANCE
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

	today = getdate(nowdate())
	shortfall_by_order = {}
	order_lines = frappe.get_all(
		"Sales Order Item",
		filters={"parent": ("in", sales_orders), "docstatus": 1},
		fields=["name", "parent", "delivery_date"],
	)
	supply_by_order_line = get_supply_by_order_line([row.name for row in order_lines])

	for row in order_lines:
		shortfall = flt(required_qty_map.get(row.name, 0)) - flt(supply_by_order_line.get(row.name, 0))
		if shortfall <= QTY_TOLERANCE:
			continue

		delivery_date = getdate(row.delivery_date) if row.delivery_date else today
		shortfall_by_order.setdefault(row.parent, {})[row.name] = {
			"required_qty": shortfall,
			"schedule_date": max(delivery_date, today),
		}

	requested = []
	blocked = []
	for name, items_needing_stock in shortfall_by_order.items():
		material_request = raise_material_request(
			frappe.get_doc("Sales Order", name), items_needing_stock, alert_on_failure=False
		)
		if material_request:
			requested.append(material_request.name)
		else:
			blocked.append(name)

	return requested, blocked


def get_supply_by_order_line(sales_order_items: list[str]) -> dict[str, float]:
	# Only the line's own requests count, never another order's: what is still to be ordered plus
	# what its purchase orders have yet to deliver. Received stock is on the shelf and already
	# shared out there, so once it goes to another order or is counted away, the line asks again.
	if not sales_order_items:
		return {}

	material_request = frappe.qb.DocType("Material Request")
	material_request_item = frappe.qb.DocType("Material Request Item")

	rows = (
		frappe.qb.from_(material_request_item)
		.inner_join(material_request)
		.on(material_request_item.parent == material_request.name)
		.select(
			material_request_item.name,
			material_request_item.sales_order_item,
			material_request_item.stock_qty,
			material_request_item.ordered_qty,
			material_request.status,
		)
		.where(
			material_request_item.sales_order_item.isin(sales_order_items)
			& (material_request.docstatus == 1)
			& (material_request.material_request_type == "Purchase")
		)
	).run(as_dict=True)

	in_transit_by_request_line = get_in_transit_qty_by_request_line([row.name for row in rows])

	supply_by_order_line = {}
	for row in rows:
		# a Stopped request buys nothing more, but whatever it already put on a purchase order is
		# still on its way
		still_to_order = 0 if row.status == "Stopped" else max(flt(row.stock_qty) - flt(row.ordered_qty), 0)
		supply_by_order_line[row.sales_order_item] = (
			supply_by_order_line.get(row.sales_order_item, 0)
			+ still_to_order
			+ in_transit_by_request_line.get(row.name, 0)
		)

	return supply_by_order_line


def get_in_transit_qty_by_request_line(material_request_items: list[str]) -> dict[str, float]:
	if not material_request_items:
		return {}

	purchase_order = frappe.qb.DocType("Purchase Order")
	purchase_order_item = frappe.qb.DocType("Purchase Order Item")

	# received_qty is in the purchase order line's own UOM, hence the conversion back to units;
	# a Closed order will send nothing more, whatever it still shows as unreceived
	rows = (
		frappe.qb.from_(purchase_order_item)
		.inner_join(purchase_order)
		.on(purchase_order_item.parent == purchase_order.name)
		.select(
			purchase_order_item.material_request_item,
			Sum(
				(purchase_order_item.qty - purchase_order_item.received_qty)
				* purchase_order_item.conversion_factor
			).as_("in_transit_qty"),
		)
		.where(
			purchase_order_item.material_request_item.isin(material_request_items)
			& (purchase_order.docstatus == 1)
			& purchase_order.status.notin(("Closed", "Completed"))
			& (purchase_order_item.qty > purchase_order_item.received_qty)
		)
		.groupby(purchase_order_item.material_request_item)
	).run(as_dict=True)

	return {row.material_request_item: flt(row.in_transit_qty) for row in rows}


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

	held_back = {row.parent for row in rows if flt(row.ordered_qty) > 0}
	held_back |= {row.parent for row in rows if not row.sales_order_item}
	held_back |= {row.parent for row in rows if row.sales_order not in sales_orders}

	return [row for row in rows if row.parent not in held_back]


def get_outstanding_order_lines(item_codes: set[str]) -> list[frappe._dict]:
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


def sync_requested_qty(sales_order_items: list[str]) -> None:
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
			material_request_item.sales_order_item.isin(sales_order_items)
			& (material_request.docstatus == 1)
			& (material_request.status != "Stopped")
		)
		.groupby(material_request_item.sales_order_item)
	).run(as_dict=True)

	live_requested_qty = {row.sales_order_item: flt(row.requested_qty) for row in rows}

	for row in frappe.get_all(
		"Sales Order Item",
		filters={"name": ("in", sales_order_items)},
		fields=["name", "requested_qty"],
	):
		live = live_requested_qty.get(row.name, 0)
		if abs(flt(row.requested_qty) - live) <= QTY_TOLERANCE:
			continue

		frappe.db.set_value("Sales Order Item", row.name, "requested_qty", live, update_modified=False)
