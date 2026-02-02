# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import frappe
from frappe.utils.data import flt


def _get_commission_threshold():
	"""Get commission threshold from settings (lazy load)."""
	return flt(frappe.db.get_single_value("Navgold Settings", "commission_threshold") or 10)


def _get_high_price_commission_adjustment():
	"""Get high price commission adjustment from settings (lazy load)."""
	return flt(frappe.db.get_single_value("Navgold Settings", "high_price_commission_adjustment") or 0.07)


def calculate_prices(costs: list, min_margin: float, max_margin: float) -> dict:
	"""
	Calculate min and max prices based on costs and margins.

	The formula works by separating costs into:
	- Absolute costs (fixed GBP amounts) - added to numerator
	- Relative costs (percentages) - subtracted from denominator
	- VAT (special handling) - converted to fraction and subtracted from denominator

	Formula: price = numerator / (denominator - margin)

	For prices > threshold, an additional commission adjustment is applied.

	Args:
		costs: List of cost dictionaries with keys:
			- type: Cost type name
			- is_absolute: bool - Fixed GBP amount
			- is_relative: bool - Percentage
			- is_vat: bool - VAT (special handling)
			- value: float - The cost value
		min_margin: Minimum margin as decimal (e.g., 0.10 for 10%)
		max_margin: Maximum margin as decimal (e.g., 0.30 for 30%)

	Returns:
		dict with 'min_price' and 'max_price' as float values
	"""
	min_margin = flt(min_margin)
	max_margin = flt(max_margin)

	# Initialize numerator and denominator
	numerator = flt(0)
	denominator = flt(1)  # 1 - relative costs - VAT fraction
	for cost in costs:
		value = flt(cost.get("value", 0))

		if cost.get("is_vat"):
			# VAT: Convert rate to fraction
			# e.g., 20% VAT -> 0.20 -> fraction = 0.20 / 1.20 = 0.1667
			vat_rate = value / 100
			vat_fraction = vat_rate / (1 + vat_rate) if vat_rate else 0
			denominator -= vat_fraction

		elif cost.get("is_absolute"):
			# Absolute cost: Add to numerator
			numerator += value

		elif cost.get("is_relative"):
			# Relative cost: Subtract percentage from denominator
			denominator -= value / 100

	# Calculate initial prices
	# formula is: price = costs / (1 - fees - margin)
	# min_price = we accept minimum margin -> use min_margin
	# max_price = we want maximum margin -> use max_margin

	min_price = _calculate_single_price(numerator, denominator, min_margin)
	max_price = _calculate_single_price(numerator, denominator, max_margin)

	# Ensure min <= max
	if min_price > max_price:
		min_price, max_price = max_price, min_price

	return {"min_price": flt(min_price, 2), "max_price": flt(max_price, 2)}


def _calculate_single_price(numerator: float, denominator: float, margin: float) -> float:
	"""
	Calculate a single price with commission adjustment for high prices.

	For prices > threshold, Amazon charges an additional commission,
	so we need to adjust the calculation.

	Args:
		numerator: Sum of absolute costs
		denominator: 1 - relative costs - VAT fraction
		margin: Target margin as decimal

	Returns:
		Calculated price as float
	"""
	commission_threshold = _get_commission_threshold()
	high_price_adjustment = _get_high_price_commission_adjustment()

	# First calculation without high-price adjustment
	adjusted_denominator = denominator - margin

	if adjusted_denominator <= 0:
		# Margin too high, would result in negative or infinite price
		return 0

	initial_price = numerator / adjusted_denominator

	# Check if price is above threshold
	if initial_price > commission_threshold:
		# Recalculate with commission adjustment
		adjusted_denominator = denominator - margin - high_price_adjustment
		if adjusted_denominator <= 0:
			return 0
		return numerator / adjusted_denominator

	return initial_price


def calculate_margin_from_price(costs: list, selling_price: float) -> dict:
	"""
	Reverse calculation: Given a selling price, calculate the achieved margin.

	Args:
		costs: List of cost dictionaries (same format as calculate_prices)
		selling_price: The selling price in GBP

	Returns:
		dict with 'margin' as percentage and 'profit' as GBP amount
	"""
	selling_price = flt(selling_price)

	if selling_price <= 0:
		return {"margin": 0, "profit": 0}

	commission_threshold = _get_commission_threshold()
	high_price_adjustment = _get_high_price_commission_adjustment()

	# Calculate total costs
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

	# Add commission adjustment for high prices
	commission_adjustment = flt(0)
	if selling_price > commission_threshold:
		commission_adjustment = high_price_adjustment

	# Calculate deductions from selling price
	vat_amount = selling_price * vat_fraction
	relative_costs = selling_price * (total_relative_rate + commission_adjustment)
	net_revenue = selling_price - vat_amount - relative_costs

	# Profit = Net revenue - absolute costs
	profit = net_revenue - total_absolute

	# Margin = Profit / Selling Price
	margin = (profit / selling_price) * 100 if selling_price > 0 else 0

	return {"margin": flt(margin, 2), "profit": flt(profit, 2)}
