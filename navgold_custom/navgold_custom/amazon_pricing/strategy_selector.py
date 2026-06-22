# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.query_builder import DocType, Order
from frappe.utils.data import flt

from navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku import get_proxy_sku_map
from navgold_custom.navgold_custom.utils import (
	get_afn_warehouse_stock_map,
	get_free_qty_map,
	get_restock_data_map,
)


def select_strategy(pricing_doc, active_strategies=None, stock_info=None) -> str | None:
	if stock_info is None:
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

	if active_strategies is None:
		active_strategies = set(
			frappe.get_all(
				"Amazon Product Strategy",
				filters={"name": ["in", all_strategies], "is_active": 1},
				pluck="name",
			)
		)

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


def get_stock_info_batch(proxy_sku_names: list) -> dict:
	if not proxy_sku_names:
		return {}

	proxy_sku_map = get_proxy_sku_map(sku_names=proxy_sku_names)
	proxy_sku_item_map = {name: info["item"] for name, info in proxy_sku_map.items()}
	all_item_codes = list({item for item in proxy_sku_item_map.values() if item})

	empty = {
		"fba_stock": 0,
		"mfn_stock": 0,
		"has_restock_date": False,
		"restock_date": None,
		"total_stock": 0,
	}

	if not all_item_codes:
		return {name: dict(empty) for name in proxy_sku_names}

	sp_api_settings = frappe.get_cached_doc("Amazon SP API Settings")
	afn_stock_map = get_afn_warehouse_stock_map(all_item_codes, sp_api_settings.afn_warehouse)
	mfn_stock_data = get_free_qty_map(all_item_codes)
	restock_map = get_restock_data_map(all_item_codes)

	result = {}
	for proxy_sku_name in proxy_sku_names:
		item_code = proxy_sku_item_map.get(proxy_sku_name)
		if not item_code:
			result[proxy_sku_name] = dict(empty)
			continue

		fba_stock = afn_stock_map.get(item_code, 0)
		mfn_stock = mfn_stock_data.get(item_code, {}).get("free_qty", 0)
		restock_date = restock_map.get(item_code)

		result[proxy_sku_name] = {
			"fba_stock": fba_stock,
			"mfn_stock": mfn_stock,
			"has_restock_date": bool(restock_date),
			"restock_date": restock_date,
			"total_stock": fba_stock + mfn_stock,
		}

	return result


def check_pending_restock(item: str) -> dict:
	restock_date = get_restock_data_map([item]).get(item)
	return {"has_restock": bool(restock_date), "restock_date": restock_date}


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


def get_active_pricing_records() -> list:
	return frappe.get_all(
		"Amazon Product Pricing",
		filters={"is_active": 1},
		fields=[
			"name",
			"proxy_sku",
			"fba_strategy",
			"sfp_strategy",
			"backorder_strategy",
			"default_strategy",
			"active_strategy",
			"fixed_price",
			"fixed_price_value",
			"current_min_price",
			"current_max_price",
		],
	)


def get_strategy_map(pricing_records: list) -> dict:
	strategy_names = {
		strategy
		for pricing in pricing_records
		for strategy in (
			pricing.fba_strategy,
			pricing.sfp_strategy,
			pricing.backorder_strategy,
			pricing.default_strategy,
		)
		if strategy
	}
	if not strategy_names:
		return {}
	rows = frappe.get_all(
		"Amazon Product Strategy",
		filters={"name": ["in", list(strategy_names)]},
		fields=["name", "is_active", "calculated_min_price", "calculated_max_price"],
	)
	return {row.name: row for row in rows}


def compute_pricing_update(
	pricing, active_strategies: set, stock_info: dict, strategy_map: dict
) -> dict | None:
	new_strategy = select_strategy(pricing, active_strategies, stock_info)

	if pricing.fixed_price:
		expected_min = flt(pricing.fixed_price_value)
		expected_max = flt(pricing.fixed_price_value)
	else:
		strategy = strategy_map.get(new_strategy) if new_strategy else None
		expected_min = flt(strategy.calculated_min_price) if strategy else 0
		expected_max = flt(strategy.calculated_max_price) if strategy else 0

	if (
		new_strategy == pricing.active_strategy
		and flt(pricing.current_min_price) == expected_min
		and flt(pricing.current_max_price) == expected_max
	):
		return None

	return {
		"active_strategy": new_strategy,
		"current_min_price": expected_min,
		"current_max_price": expected_max,
	}


@frappe.whitelist()
def bulk_refresh_strategies():
	pricing_records = get_active_pricing_records()
	if not pricing_records:
		return {"total": 0, "updated": 0}

	strategy_map = get_strategy_map(pricing_records)
	active_strategies = {name for name, row in strategy_map.items() if row.is_active}

	proxy_sku_names = [pricing.proxy_sku for pricing in pricing_records if pricing.proxy_sku]
	stock_info_map = get_stock_info_batch(proxy_sku_names)

	updates = {}
	for pricing in pricing_records:
		try:
			stock_info = stock_info_map.get(pricing.proxy_sku, {})
			update_values = compute_pricing_update(pricing, active_strategies, stock_info, strategy_map)
			if update_values is None:
				continue
			updates[pricing.name] = update_values
		except Exception as e:
			frappe.log_error(f"Strategy refresh error for {pricing.name}: {e!s}", "Strategy Refresh")

	if updates:
		frappe.db.bulk_update(
			"Amazon Product Pricing",
			updates,
			update_modified=False,
		)

	# Stamp completion so the scheduled submit's stagger guard can confirm a fresh refresh ran.
	frappe.db.set_single_value("Navgold Settings", "last_strategy_refresh_at", frappe.utils.now_datetime())

	return {"total": len(pricing_records), "updated": len(updates)}
