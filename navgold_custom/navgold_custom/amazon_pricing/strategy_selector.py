# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.query_builder import DocType, Order
from frappe.query_builder.functions import Count

from navgold_custom.navgold_custom.utils import get_free_qty_map


def select_strategy(pricing_doc) -> str | None:
	stock_info = get_stock_info(pricing_doc.proxy_sku)
	all_strategies = [
		strategy
		for strategy in [
			pricing_doc.fba_strategy,
			pricing_doc.sfp_strategy,
			pricing_doc.backorder_strategy,
			pricing_doc.default_strategy,
		]
		if strategy
	]

	if not all_strategies:
		return None

	active_strategies = set(
		frappe.get_all(
			"Amazon Product Strategy",
			filters={"name": ["in", all_strategies], "is_active": 1},
			pluck="name",
		)
	)
	# return pricing_doc.backorder_strategy
	# Priority 1: FBA Strategy
	if pricing_doc.fba_strategy in active_strategies and stock_info.get("fba_stock", 0) > 0:
		return pricing_doc.fba_strategy

	# Priority 2: SFP Strategy (Seller Fulfilled Prime, when system has some stock)
	if pricing_doc.sfp_strategy in active_strategies and stock_info.get("total_stock", 0) > 0:
		return pricing_doc.sfp_strategy
	# Priority 3: Backorder Strategy (when has restock date OR no stock at all)
	if pricing_doc.backorder_strategy in active_strategies and (
		stock_info.get("has_restock_date", False) or stock_info.get("total_stock", 0) <= 0
	):
		return pricing_doc.backorder_strategy

	# Priority 4: Default Strategy (fallback)
	if pricing_doc.default_strategy in active_strategies:
		return pricing_doc.default_strategy

	return None


# Don't account for material request
def get_stock_info(proxy_sku: str) -> dict:
	"""
	This function checks:
	- FBA warehouse stock (AFN warehouse)
	- MFN warehouse stock
	- Restock date information
	"""
	proxy = frappe.get_cached_doc("Proxy SKU", proxy_sku)

	sp_api_settings = frappe.get_single("Amazon SP API Settings")

	fba_stock = 0
	mfn_stock = 0

	if sp_api_settings.afn_warehouse:
		afn_qty = (
			frappe.db.get_value(
				"Bin", {"item_code": proxy.item, "warehouse": sp_api_settings.afn_warehouse}, "actual_qty"
			)
			or 0
		)
		fba_stock += afn_qty

	mfn_qty = get_free_qty_map([proxy.item]).get(proxy.item, {}).get("free_qty", 0)
	mfn_stock += mfn_qty

	restock_info = check_pending_restock(proxy.item)

	return {
		"fba_stock": fba_stock,
		"mfn_stock": mfn_stock,
		"has_restock_date": restock_info["has_restock"],
		"restock_date": restock_info["restock_date"],
		"total_stock": fba_stock + mfn_stock,
	}


def check_pending_restock(item: str) -> dict:
	"""
	Check if there's a pending restock for this item.

	Checks:
	- Open Material Requests with expected delivery date
	- Open Purchase Orders with expected delivery date
	"""

	purchase_order = DocType("Purchase Order")
	purchase_order_item = DocType("Purchase Order Item")

	po_items = (
		frappe.qb.from_(purchase_order)
		.join(purchase_order_item)
		.on(purchase_order.name == purchase_order_item.parent)
		.select(purchase_order_item.schedule_date)
		.where(
			(purchase_order.docstatus == 1)
			& (purchase_order.status.notin(["Closed", "Cancelled", "Completed"]))
			& (purchase_order_item.item_code == item)
		)
		.orderby(purchase_order_item.schedule_date, order=Order.desc)
		.limit(1)
	).run(as_dict=True)

	restock_date = po_items[0].schedule_date if po_items else None
	has_restock = bool(restock_date)

	return {"has_restock": has_restock, "restock_date": restock_date}


def refresh_active_strategy(pricing_name: str):
	pricing = frappe.get_doc("Amazon Product Pricing", pricing_name)
	old_strategy = pricing.active_strategy

	pricing.select_active_strategy()
	pricing.update_current_prices()

	if pricing.active_strategy != old_strategy:
		pricing.save()
		frappe.msgprint(
			_("Active strategy changed from {0} to {1}").format(
				old_strategy or "None", pricing.active_strategy or "None"
			)
		)

	return pricing.active_strategy


@frappe.whitelist()
def bulk_refresh_strategies():
	pricing_records = frappe.get_all("Amazon Product Pricing", filters={"is_active": 1}, pluck="name")

	updated = 0
	for pricing_name in pricing_records:
		try:
			pricing = frappe.get_cached_doc("Amazon Product Pricing", pricing_name)
			old_strategy = pricing.active_strategy

			pricing.select_active_strategy()
			pricing.update_current_prices()

			if pricing.active_strategy != old_strategy:
				pricing.save()
				updated += 1
		except Exception as e:
			frappe.log_error(f"Strategy refresh error for {pricing_name}: {e!s}", "Strategy Refresh")

	return {"total": len(pricing_records), "updated": updated}
