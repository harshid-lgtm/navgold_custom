# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.utils.data import flt

from navgold_custom.navgold_custom.amazon_pricing.price_calculator import (
	calculate_margin_from_price,
	calculate_prices,
)


@frappe.whitelist()
def calculate_margin_for_strategy(strategy_name: str, target_price: float) -> dict:
	"""
	Calculate the margin achieved at a given target price for a strategy.

	This is used by the UI to show real-time margin calculations.

	"""

	target_price = flt(target_price)

	if target_price <= 0:
		return {"margin": 0, "profit": 0}

	strategy = frappe.get_cached_doc("Amazon Product Strategy", strategy_name)

	active_costs = [cost for cost in strategy.costs if cost.is_active]

	if not active_costs:
		return {"margin": 0, "profit": 0}

	cost_data = []
	for cost in active_costs:
		cost_type = frappe.get_cached_doc("Cost Type", cost.cost_type)
		cost_data.append(
			{
				"type": cost.cost_type,
				"is_absolute": cost_type.is_absolute,
				"is_relative": cost_type.is_relative,
				"is_vat": cost_type.is_vat,
				"value": cost.value,
			}
		)

	return calculate_margin_from_price(costs=cost_data, selling_price=target_price)


@frappe.whitelist()
def calculate_prices_for_strategy(strategy_name: str) -> dict:
	"""
	Recalculate prices for a strategy and return them.

	Useful for refreshing prices after cost changes.

	"""

	strategy = frappe.get_cached_doc("Amazon Product Strategy", strategy_name)

	active_costs = [cost for cost in strategy.costs if cost.is_active]

	if not active_costs:
		return {"min_price": 0, "max_price": 0}

	cost_data = []
	for cost in active_costs:
		cost_type = frappe.get_cached_doc("Cost Type", cost.cost_type)
		cost_data.append(
			{
				"type": cost.cost_type,
				"is_absolute": cost_type.is_absolute,
				"is_relative": cost_type.is_relative,
				"is_vat": cost_type.is_vat,
				"value": cost.value,
			}
		)

	return calculate_prices(
		costs=cost_data, min_margin=flt(strategy.min_margin) / 100, max_margin=flt(strategy.max_margin) / 100
	)
