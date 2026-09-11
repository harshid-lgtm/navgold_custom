import math

import frappe
from frappe.query_builder import DocType, Order
from frappe.query_builder.functions import Coalesce, Sum
from frappe.utils import flt
from frappe.utils.caching import redis_cache

BATCH_SIZE = 500
ITEM_AFN_FIELD = "custom_amazon_afn_stock"


def bulk_set_value(doctype: str, names: list, field: str, value, batch_size: int = BATCH_SIZE):
	for i in range(0, len(names), batch_size):
		batch = names[i : i + batch_size]
		frappe.db.set_value(doctype, {"name": ["in", batch]}, field, value)


def get_afn_warehouse_stock_map(item_codes: list[str], warehouse: str) -> dict[str, float]:
	if not item_codes or not warehouse:
		return {}

	bin_dt = DocType("Bin")
	rows = (
		frappe.qb.from_(bin_dt)
		.select(bin_dt.item_code, Sum(bin_dt.actual_qty).as_("actual_qty"))
		.where((bin_dt.item_code.isin(item_codes)) & (bin_dt.warehouse == warehouse))
		.groupby(bin_dt.item_code)
	).run(as_dict=True)

	return {row.item_code: flt(row.actual_qty) for row in rows}


def get_fba_stock_map(item_codes: list[str], afn_warehouse: str | None) -> dict[str, float]:
	if not item_codes:
		return {}

	bin_stock_map = get_afn_warehouse_stock_map(item_codes, afn_warehouse)

	if not frappe.get_cached_doc("Navgold Settings").enable_afn_stock_sync:
		return bin_stock_map

	fetched_rows = frappe.get_all(
		"Item",
		filters={"name": ["in", item_codes]},
		fields=["name", ITEM_AFN_FIELD],
	)
	fetched_stock_map = {row.name: flt(row.get(ITEM_AFN_FIELD)) for row in fetched_rows}

	# FBA Transfer stock entries land in the AFN warehouse Bin at packing time, before Amazon's
	# inventory report counts them as fulfillable. Taking the max keeps the FBA strategy selected
	# during the inbound transit window while still seeing stock only Amazon knows about.
	return {
		item_code: max(flt(bin_stock_map.get(item_code)), fetched_stock_map.get(item_code, 0.0))
		for item_code in item_codes
	}


def get_restock_data_map(item_codes: list[str]) -> dict[str, object]:
	if not item_codes:
		return {}

	purchase_order = DocType("Purchase Order")
	purchase_order_item = DocType("Purchase Order Item")
	rows = (
		frappe.qb.from_(purchase_order)
		.join(purchase_order_item)
		.on(purchase_order.name == purchase_order_item.parent)
		.select(purchase_order_item.item_code, purchase_order_item.schedule_date)
		.where(
			(purchase_order.docstatus == 1)
			& (purchase_order.status.notin(["Closed", "Cancelled", "Completed"]))
			& (purchase_order_item.received_qty < purchase_order_item.qty)
			& (purchase_order_item.item_code.isin(item_codes))
		)
		# soonest first: with more than one line open the item is back when the earliest lands, and
		# the latest date would have us promise the customer the worst of them
		.orderby(purchase_order_item.schedule_date, order=Order.asc)
	).run(as_dict=True)

	restock_map: dict[str, object] = {}
	for row in rows:
		if row.item_code not in restock_map:
			restock_map[row.item_code] = row.schedule_date
	return restock_map


def get_free_qty_map(item_codes: list[str], deduct_draft_orders: bool = False) -> dict[str, dict[str, float]]:
	"""Physically sellable qty per item: what is on the shelf less what submitted orders reserve.

	``deduct_draft_orders`` additionally nets off open MFN draft Amazon orders. Only purchasing
	wants that: a draft is real committed demand, but it reserves nothing in Bin, so counting it
	as gone would tell the ladder to backorder stock that is sitting on the shelf.
	"""
	if not item_codes:
		return {}

	bin_dt = DocType("Bin")
	warehouse_dt = DocType("Warehouse")

	result = (
		frappe.qb.from_(bin_dt)
		.left_join(warehouse_dt)
		.on(bin_dt.warehouse == warehouse_dt.name)
		.select(
			bin_dt.item_code,
			Sum(bin_dt.actual_qty).as_("actual_qty"),
			(Sum(bin_dt.actual_qty) - Sum(bin_dt.reserved_qty)).as_("free_qty"),
		)
		.where((bin_dt.item_code.isin(item_codes)) & (warehouse_dt.is_rejected_warehouse == 0))
		.groupby(bin_dt.item_code)
	).run(as_dict=True)

	draft_qty_map = get_draft_amazon_order_qty_map(item_codes) if deduct_draft_orders else {}

	return {
		r["item_code"]: {
			"actual_qty": flt(r.get("actual_qty", 0)),
			"free_qty": flt(r.get("free_qty", 0)) - draft_qty_map.get(r["item_code"], 0),
		}
		for r in result
	}


def get_allocatable_warehouse_filter(warehouse_field):
	from navgold_custom.navgold_custom.custom_script.pick_list.pick_list import (
		get_delivery_consolidation_warehouse,
	)

	warehouse_dt = DocType("Warehouse")

	allocatable = warehouse_dt.is_rejected_warehouse == 0
	consolidation_warehouse = get_delivery_consolidation_warehouse()
	if consolidation_warehouse:
		allocatable = allocatable | (warehouse_field == consolidation_warehouse)

	return allocatable


def get_shelf_actual_qty_map(item_codes: list[str]) -> dict[str, float]:
	if not item_codes:
		return {}

	bin_dt = DocType("Bin")
	warehouse_dt = DocType("Warehouse")

	rows = (
		frappe.qb.from_(bin_dt)
		.left_join(warehouse_dt)
		.on(bin_dt.warehouse == warehouse_dt.name)
		.select(bin_dt.item_code, Sum(bin_dt.actual_qty).as_("actual_qty"))
		.where(bin_dt.item_code.isin(item_codes) & get_allocatable_warehouse_filter(bin_dt.warehouse))
		.groupby(bin_dt.item_code)
	).run(as_dict=True)

	return {row.item_code: flt(row.actual_qty) for row in rows}


def get_draft_amazon_order_qty_map(item_codes: list[str]) -> dict[str, float]:
	if not item_codes:
		return {}

	# imported inside the function: amazon_repository pulls in the SP-API client and the alert
	# stack at module level, and every caller of utils would carry that weight
	from navgold_custom.navgold_custom.amazon_repository import CLOSED_ORDER_STATUSES

	sales_order = DocType("Sales Order")
	sales_order_item = DocType("Sales Order Item")

	rows = (
		frappe.qb.from_(sales_order_item)
		.join(sales_order)
		.on(sales_order_item.parent == sales_order.name)
		.select(sales_order_item.item_code, Sum(sales_order_item.stock_qty).as_("draft_qty"))
		.where(
			sales_order_item.item_code.isin(item_codes)
			& (sales_order.docstatus == 0)
			& (sales_order.amazon_order_id.notnull())
			& (sales_order.amazon_order_id != "")
			# a draft stays a draft once Amazon cancels it — nothing submits or deletes it, so
			# without this it eats free qty forever
			& Coalesce(sales_order.amazon_order_status, "").notin(list(CLOSED_ORDER_STATUSES))
			# this deduction stands in for the reservation a draft never takes, and only a
			# merchant-fulfilled order will ever draw on our shelf: an AFN order ships from
			# Amazon's own warehouse and is closed on submit, so deducting it hid real stock
			& (sales_order.fulfillment_channel == "MFN")
		)
		.groupby(sales_order_item.item_code)
	).run(as_dict=True)

	return {row["item_code"]: flt(row.get("draft_qty", 0)) for row in rows}


def calculate_adjusted_qty(raw_qty: float, pool_size: int, pack_size: float) -> int:
	if pool_size <= 0 or pack_size <= 0:
		return 0
	return max(math.floor(raw_qty / pool_size / pack_size), 0)


def allocate_pack_stock(available_qty: float, pack_size_by_proxy_sku: dict[str, float]) -> dict[str, int]:
	allocation = {proxy_sku: 0 for proxy_sku in pack_size_by_proxy_sku}

	claimants = sorted(
		(
			(flt(pack_size), proxy_sku)
			for proxy_sku, pack_size in pack_size_by_proxy_sku.items()
			if flt(pack_size) > 0
		),
		key=lambda claimant: (claimant[0], claimant[1]),
	)
	remaining_qty = flt(available_qty)
	if not claimants or remaining_qty <= 0:
		return allocation

	# Deal the whole rounds in one step: a pack-of-1 against a large free qty would otherwise
	# take one loop pass per unit.
	units_per_round = sum(pack_size for pack_size, _proxy_sku in claimants)
	full_rounds = int(remaining_qty // units_per_round)
	if full_rounds:
		remaining_qty = flt(remaining_qty - full_rounds * units_per_round, 9)
		for _pack_size, proxy_sku in claimants:
			allocation[proxy_sku] = full_rounds

	while True:
		allocated_in_pass = False
		for pack_size, proxy_sku in claimants:
			if remaining_qty >= pack_size:
				remaining_qty = flt(remaining_qty - pack_size, 9)
				allocation[proxy_sku] += 1
				allocated_in_pass = True
		if not allocated_in_pass:
			return allocation


def get_warehouse_pack_pool_map(item_codes: list[str]) -> dict[str, dict[str, float]]:
	if not item_codes:
		return {}

	# An FBA SKU is held out of the pool only while Amazon actually holds stock for it: it sells
	# that stock from Amazon's warehouse and must not dilute ours. Once Amazon runs dry it falls
	# back to shipping off our shelves, so it claims packs alongside the MFN SKUs.
	# afn_stock is only refreshed while the sync is on; off means it is stale, so treat FBA as out
	# of stock — the same stance select_strategy takes.
	sync_on = frappe.get_cached_doc("Navgold Settings").enable_afn_stock_sync

	proxy_sku_dt = DocType("Proxy SKU")
	query = frappe.qb.from_(proxy_sku_dt).select(
		proxy_sku_dt.name, proxy_sku_dt.item, proxy_sku_dt.uom, proxy_sku_dt.is_active
	)
	if sync_on:
		query = query.where(
			(proxy_sku_dt.item.isin(item_codes))
			& ((proxy_sku_dt.is_fba == 0) | (Coalesce(proxy_sku_dt.afn_stock, 0) <= 0))
		)
	else:
		query = query.where(proxy_sku_dt.item.isin(item_codes))

	claimant_rows = query.run(as_dict=True)

	if not claimant_rows:
		return {}

	rows_by_item = {}
	for row in claimant_rows:
		rows_by_item.setdefault(row.item, []).append(row)

	# An item with no active SKU keeps the historical "pool of one" behaviour rather than
	# silently dropping every listing's stock to zero.
	pool_rows = []
	for item_rows in rows_by_item.values():
		active_rows = [row for row in item_rows if row.is_active]
		pool_rows.extend(active_rows or item_rows)

	uom_conversion_dt = DocType("UOM Conversion Detail")
	uom_rows = (
		frappe.qb.from_(uom_conversion_dt)
		.select(uom_conversion_dt.parent, uom_conversion_dt.uom, uom_conversion_dt.conversion_factor)
		.where((uom_conversion_dt.parenttype == "Item") & (uom_conversion_dt.parent.isin(item_codes)))
	).run(as_dict=True)
	uom_factor_map = {(row.parent, row.uom): flt(row.conversion_factor) or 1.0 for row in uom_rows}

	pool_map = {}
	for row in pool_rows:
		pool_map.setdefault(row.item, {})[row.name] = uom_factor_map.get((row.item, row.uom), 1.0)

	return pool_map


def apply_stock_reserve(available_qty: float, reserve_percentage: float) -> float:
	"""A reserve holds back a share of the stock itself, so it is taken off the units before they are
	dealt into packs. Taken off the pack count instead it rounds a single pack away to nothing."""
	reserve_percentage = max(0.0, min(flt(reserve_percentage), 100.0))
	# round the product before it is dealt: 40 at 90% lands on 3.999999999999999 in binary, and a
	# pack of 4 would read that as nothing rather than the one whole pack the reserve leaves
	return max(flt(flt(available_qty) * (1 - reserve_percentage / 100), 9), 0.0)


def get_pack_allocation_map(
	available_qty_by_item: dict[str, float], reserve_percentage: float = 0
) -> dict[str, int]:
	if not available_qty_by_item:
		return {}

	allocation_map = {}
	for item_code, pack_size_by_proxy_sku in get_warehouse_pack_pool_map(list(available_qty_by_item)).items():
		sellable_qty = apply_stock_reserve(available_qty_by_item.get(item_code, 0), reserve_percentage)
		allocation_map.update(allocate_pack_stock(sellable_qty, pack_size_by_proxy_sku))

	return allocation_map


@redis_cache()
@frappe.whitelist()
def get_conversion_factor(item_code: str, uom: str):
	if not uom or uom == "Nos":
		return 1.0
	conversion_factor = frappe.db.get_value(
		"UOM Conversion Detail", {"parent": item_code, "uom": uom}, "conversion_factor"
	)
	return flt(conversion_factor) or 1.0


class AmazonFrozenError(frappe.ValidationError):
	# Distinct from a real transport/API failure so callers can mark records "Skipped"
	# instead of polluting the failed-sync triage queue with deliberate blocks.
	pass


class PricingNotApprovedError(frappe.ValidationError):
	# Same intent as the freeze error: a Dry Run block is an intentional gate, not a failure.
	pass


def block_if_amazon_frozen():
	# Deliberately uncached: this is the emergency kill switch and must take effect
	# on long-running workers without a cache clear.
	if frappe.db.get_single_value("Navgold Settings", "freeze_amazon_outbound"):
		raise AmazonFrozenError(frappe._("Amazon outbound writes are frozen via Navgold Settings."))


def block_if_pricing_not_approved():
	# Read uncached for the same reason as the freeze guard: the Dry Run / Live approval
	# state must take effect immediately on long-running workers without a cache clear.
	if frappe.db.get_single_value("Navgold Settings", "pricing_sync_mode") != "Live":
		raise PricingNotApprovedError(
			frappe._(
				"Pricing sync is in Dry Run mode — push blocked until approved "
				"(Navgold Settings → Approve & Go Live)."
			)
		)
