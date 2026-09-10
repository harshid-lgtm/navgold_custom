# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt, getdate

from navgold_custom.navgold_custom.utils import get_shelf_actual_qty_map

OPEN_STATUSES = ("Pending", "Partially Ordered", "Partially Received")
SETTLED_SALES_ORDER_STATUSES = ("Closed", "Completed")


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
	"""Re-decide every open order's coverage against the shelf as it stands now.

	The submit-time decision is taken once, against the shelf of that moment, and nothing revisits
	it. Both directions go stale: a pack bought whole for one order leaves a surplus that would
	cover the next one, and a unit an order was counting on can leave on someone else's delivery
	note. This walks both back to the truth — retiring the requests the shelf now covers, and
	raising fresh ones for the orders left short.
	"""
	from navgold_custom.navgold_custom.custom_script.sales_order.sales_order import (
		get_orders_open_for_supply,
		request_stock_for_uncovered_orders,
	)

	sales_orders = get_orders_open_for_supply()
	if not sales_orders:
		return {"retired": [], "requested": []}

	# retire first: an order the shelf already covers must drop its request before the top-up runs,
	# or the same pass would raise a request and then stop it again on the next one
	retired = retire_demand_covered_by_shelf(sales_orders)
	requested = request_stock_for_uncovered_orders(sales_orders)

	return {"retired": retired, "requested": requested}


def retire_demand_covered_by_shelf(sales_orders: list[str]) -> list[str]:
	material_requests = get_open_material_requests(sales_orders)
	if not material_requests:
		return []

	retirable_items = get_retirable_material_request_items(material_requests)
	if not retirable_items:
		return []

	covered_sales_order_items = get_sales_order_items_covered_by_shelf(
		{row.item_code for row in retirable_items}
	)

	claimed_by = {}
	for row in retirable_items:
		claimed_by.setdefault(row.parent, []).append(row)

	retired = []
	for name, rows in claimed_by.items():
		if any(row.sales_order_item not in covered_sales_order_items for row in rows):
			continue

		material_request = frappe.get_doc("Material Request", name)
		material_request.update_status("Stopped")
		release_requested_qty(rows)
		retired.append(name)

	return retired


def get_retirable_material_request_items(material_requests: list[str]) -> list[frappe._dict]:
	"""Lines whose demand we are still free to withdraw.

	A line already on a Purchase Order is not one of them. Stopping its request would not unbuy
	the stock, and erpnext guards every Material Request status rule with ``status != "Stopped"``,
	so the document would never move again once the goods land.
	"""
	rows = frappe.get_all(
		"Material Request Item",
		filters={"parent": ("in", material_requests), "docstatus": 1},
		fields=["parent", "name", "item_code", "stock_qty", "ordered_qty", "sales_order_item"],
	)

	ordered_against = {row.parent for row in rows if flt(row.ordered_qty) > 0}
	unlinked = {row.parent for row in rows if not row.sales_order_item}

	return [row for row in rows if row.parent not in ordered_against and row.parent not in unlinked]


def get_sales_order_items_covered_by_shelf(item_codes: set[str]) -> set[str]:
	"""Sales Order lines the shelf can supply once the orders ahead of them have taken their share.

	Earliest delivery date wins, because that is the order the warehouse will pick in and the one
	whose promise breaks first. The tie-break on line name only exists to keep the answer stable:
	an allocation that flips between runs would retire a request on one pass and raise it again on
	the next.
	"""
	if not item_codes:
		return set()

	order_lines = get_outstanding_order_lines(item_codes)
	if not order_lines:
		return set()

	shelf_qty_map = get_shelf_actual_qty_map(list(item_codes))

	covered = set()
	for row in sorted(order_lines, key=lambda line: (getdate(line.delivery_date), line.name)):
		outstanding = flt(row.stock_qty) - flt(row.delivered_qty)
		if outstanding <= 0:
			continue

		available = shelf_qty_map.get(row.item_code, 0)
		if available < outstanding:
			continue

		shelf_qty_map[row.item_code] = available - outstanding
		covered.add(row.name)

	return covered


def get_outstanding_order_lines(item_codes: set[str]) -> list[frappe._dict]:
	"""Every line still owed to a customer, which is exactly the set erpnext holds in
	``Bin.reserved_qty``. Orders being picked and orders Amazon has already shipped are in here on
	purpose: they still hold their stock, and crediting it to somebody else would oversell it."""
	sales_order = frappe.qb.DocType("Sales Order")
	sales_order_item = frappe.qb.DocType("Sales Order Item")

	return (
		frappe.qb.from_(sales_order_item)
		.join(sales_order)
		.on(sales_order_item.parent == sales_order.name)
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
		)
	).run(as_dict=True)


def release_requested_qty(rows: list[frappe._dict]) -> None:
	"""Hand the retired quantity back to the Sales Order line.

	erpnext maintains ``requested_qty`` through the status updater on submit and cancel only —
	stopping a request leaves it standing. Since the top-up caps each line at
	``stock_qty - requested_qty``, an untouched value would make retirement a one-way door: the
	order could never ask for stock again when the shelf that covered it went elsewhere.
	"""
	for row in rows:
		requested_qty = flt(frappe.db.get_value("Sales Order Item", row.sales_order_item, "requested_qty"))
		frappe.db.set_value(
			"Sales Order Item",
			row.sales_order_item,
			"requested_qty",
			max(requested_qty - flt(row.stock_qty), 0),
			update_modified=False,
		)
