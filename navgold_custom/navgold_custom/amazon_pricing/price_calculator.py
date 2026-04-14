# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import frappe
from frappe.utils.data import flt


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

		if cost.get("is_vat"):
			vat_rate = value / 100
			vat_fraction = vat_rate / (1 + vat_rate) if vat_rate else 0
			denominator -= vat_fraction

		elif cost.get("is_absolute"):
			numerator += value

		elif cost.get("is_relative"):
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

		if cost.get("is_vat"):
			vat_rate = value / 100
			vat_fraction = vat_rate / (1 + vat_rate) if vat_rate else 0

		elif cost.get("is_absolute"):
			total_absolute += value

		elif cost.get("is_relative"):
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
