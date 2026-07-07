# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.utils.caching import redis_cache
from frappe.utils.data import flt

# The only two value modes a cost row can carry: Auto (sub-type resolves the feed) or Manual (pinned value).
VALUE_MODES = {"Auto", "Manual"}


@redis_cache()
def get_cost_sub_type_map() -> dict:
	# One query feeds every cost row's dispatch; the leaves are small reference data. Cost sub types are
	# the leaf nodes (is_group=0) of the Cost Type tree — they carry where a value comes from.
	return {
		row.name: {"source": row.source, "default_value": row.default_value}
		for row in frappe.get_all(
			"Cost Type", filters={"is_group": 0}, fields=["name", "source", "default_value"]
		)
	}


@redis_cache()
def get_cost_type_map() -> dict:
	# Groups (is_group=1) carry the calculation rule; leaves hold no calculation_type.
	# is_purchase_cost rides along for the push-block rules in pricing_validators.
	rows = frappe.get_all(
		"Cost Type",
		filters={"is_group": 1},
		fields=["name", "calculation_type", "is_purchase_cost"],
	)
	return {row.name: row for row in rows}


def get_item_tax_rate(item_code: str) -> float:
	if not item_code:
		return flt(0)

	template = frappe.db.get_value(
		"Item Tax",
		{"parent": item_code, "parenttype": "Item"},
		"item_tax_template",
		order_by="idx asc",
	)
	if not template:
		return flt(0)

	rate = frappe.db.get_value(
		"Item Tax Template Detail",
		{"parent": template},
		"tax_rate",
		order_by="idx asc",
	)
	return flt(rate)


def get_cost_data(
	strategy, override_supplier_price: float | None = None, cost_sub_type_map: dict | None = None
) -> list[dict]:
	active_costs = [cost for cost in strategy.costs if cost.is_active]
	if not active_costs:
		return []

	cost_type_map = get_cost_type_map()
	if cost_sub_type_map is None:
		cost_sub_type_map = get_cost_sub_type_map()

	cost_data = []
	for cost in active_costs:
		cost_type = cost_type_map.get(cost.cost_type)
		if not cost_type:
			continue

		sub_type_source = (cost_sub_type_map.get(cost.cost_sub_type) or {}).get("source")
		is_supplier_fed = sub_type_source == "Supplier-Sync" and cost.value_mode != "Manual"
		if is_supplier_fed and override_supplier_price is not None:
			value = flt(override_supplier_price)
		else:
			value = flt(cost.value)

		cost_data.append(
			{
				"type": cost.cost_type,
				"calculation_type": cost_type.calculation_type,
				"value": value,
			}
		)

	return cost_data


def get_pricing_settings():
	settings = frappe.get_cached_doc("Navgold Settings")
	return {
		"commission_threshold": flt(settings.commission_threshold or 10),
		"high_price_adjustment": flt(settings.high_price_commission_adjustment or 0.07),
	}


def calculate_prices(costs: list, min_margin: float, max_margin: float) -> dict:
	min_margin = flt(min_margin)
	max_margin = flt(max_margin)
	settings = get_pricing_settings()

	numerator = flt(0)
	denominator = flt(1)
	for cost in costs:
		value = flt(cost.get("value", 0))
		calculation_type = cost.get("calculation_type")

		if calculation_type == "VAT":
			vat_rate = value / 100
			vat_fraction = vat_rate / (1 + vat_rate) if vat_rate else 0
			denominator -= vat_fraction

		elif calculation_type == "Absolute":
			numerator += value

		elif calculation_type == "Relative":
			denominator -= value / 100

	min_price = calculate_single_price(numerator, denominator, min_margin, settings)
	max_price = calculate_single_price(numerator, denominator, max_margin, settings)

	if min_price > max_price:
		min_price, max_price = max_price, min_price

	return {"min_price": flt(min_price, 2), "max_price": flt(max_price, 2)}


def calculate_single_price(numerator: float, denominator: float, margin: float, settings: dict) -> float:
	commission_threshold = settings["commission_threshold"]
	high_price_adjustment = settings["high_price_adjustment"]

	adjusted_denominator = denominator - margin

	if adjusted_denominator <= 0:
		return 0

	initial_price = numerator / adjusted_denominator

	if initial_price > commission_threshold:
		adjusted_denominator = denominator - margin - high_price_adjustment
		if adjusted_denominator <= 0:
			return 0
		return numerator / adjusted_denominator

	return initial_price


def calculate_prices_with_purchase_cost_override(
	strategy, override_purchase_cost: float, cost_sub_type_map: dict | None = None
) -> dict:
	cost_data = get_cost_data(
		strategy, override_supplier_price=flt(override_purchase_cost), cost_sub_type_map=cost_sub_type_map
	)
	if not cost_data:
		return {"min_price": 0, "max_price": 0}

	return calculate_prices(
		costs=cost_data,
		min_margin=flt(strategy.min_margin) / 100,
		max_margin=flt(strategy.max_margin) / 100,
	)


def calculate_margin_from_price(costs: list, selling_price: float) -> dict:
	selling_price = flt(selling_price)

	if selling_price <= 0:
		return {"margin": 0, "profit": 0}

	settings = get_pricing_settings()
	commission_threshold = settings["commission_threshold"]
	high_price_adjustment = settings["high_price_adjustment"]

	total_absolute = flt(0)
	total_relative_rate = flt(0)
	vat_fraction = flt(0)

	for cost in costs:
		value = flt(cost.get("value", 0))
		calculation_type = cost.get("calculation_type")

		if calculation_type == "VAT":
			vat_rate = value / 100
			vat_fraction = vat_rate / (1 + vat_rate) if vat_rate else 0

		elif calculation_type == "Absolute":
			total_absolute += value

		elif calculation_type == "Relative":
			total_relative_rate += value / 100

	commission_adjustment = flt(0)
	if selling_price > commission_threshold:
		commission_adjustment = high_price_adjustment

	vat_amount = selling_price * vat_fraction
	relative_costs = selling_price * (total_relative_rate + commission_adjustment)
	net_revenue = selling_price - vat_amount - relative_costs

	profit = net_revenue - total_absolute
	margin = (profit / selling_price) * 100 if selling_price > 0 else 0

	return {"margin": flt(margin, 2), "profit": flt(profit, 2)}
