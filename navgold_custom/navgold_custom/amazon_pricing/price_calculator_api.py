# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import frappe
from frappe.utils.data import flt

from navgold_custom.navgold_custom.amazon_pricing.price_calculator import (
	calculate_margin_from_price,
	calculate_prices,
	get_cost_data,
)


@frappe.whitelist()
def calculate_margin_for_strategy(strategy_name: str, target_price: float) -> dict:
	target_price = flt(target_price)

	if target_price <= 0:
		return {"margin": 0, "profit": 0}

	strategy = frappe.get_cached_doc("Amazon Product Strategy", strategy_name)
	cost_data = get_cost_data(strategy)

	if not cost_data:
		return {"margin": 0, "profit": 0}

	return calculate_margin_from_price(costs=cost_data, selling_price=target_price)


@frappe.whitelist()
def calculate_prices_for_strategy(strategy_name: str) -> dict:
	strategy = frappe.get_cached_doc("Amazon Product Strategy", strategy_name)
	cost_data = get_cost_data(strategy)

	if not cost_data:
		return {"min_price": 0, "max_price": 0}

	return calculate_prices(
		costs=cost_data, min_margin=flt(strategy.min_margin) / 100, max_margin=flt(strategy.max_margin) / 100
	)
