# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from frappe.tests import IntegrationTestCase, change_settings
from frappe.utils.data import flt

from navgold_custom.navgold_custom.amazon_pricing.price_calculator import (
	calculate_margin_from_price,
	calculate_prices,
	calculate_single_price,
)

# calculate_single_price takes settings as an argument, so those cases pass this dict directly and stay
# pure. The DB-reading functions (calculate_prices / calculate_margin_from_price) read the real Navgold
# Settings singleton via get_pricing_settings, so their tests pin it with @change_settings instead.
# commission_threshold=10 / 0.07 adjustment are the documented get_pricing_settings defaults.
SETTINGS_ARG = {"commission_threshold": 10, "high_price_adjustment": 0.07}
PINNED_SETTINGS = {"commission_threshold": 10, "high_price_commission_adjustment": 0.07}


def absolute(value):
	return {"calculation_type": "Absolute", "value": value}


def relative(value):
	return {"calculation_type": "Relative", "value": value}


def vat(value):
	return {"calculation_type": "VAT", "value": value}


class TestPriceCalculator(IntegrationTestCase):
	"""Money-path coverage for the Amazon repricing formula.

	Expected values are derived by hand from the documented spec (numerator / (denominator - margin)),
	NOT read off the implementation. The arithmetic is spelled out in each test so a reviewer can
	re-check every number without running the code.
	"""

	def assertPriceEqual(self, actual, expected):
		# Prices come back rounded to 2 dp; compare at cent precision.
		self.assertAlmostEqual(flt(actual), flt(expected), places=2)

	@change_settings("Navgold Settings", PINNED_SETTINGS)
	def test_absolute_cost_only(self):
		# numerator = 5, denominator = 1 (no relative/vat).
		# min: 5 / (1 - 0.20) = 5 / 0.80 = 6.25
		# max: 5 / (1 - 0.40) = 5 / 0.60 = 8.3333 -> 8.33
		result = calculate_prices([absolute(5)], min_margin=0.20, max_margin=0.40)
		self.assertPriceEqual(result["min_price"], 6.25)
		self.assertPriceEqual(result["max_price"], 8.33)

	@change_settings("Navgold Settings", PINNED_SETTINGS)
	def test_relative_cost_reduces_denominator(self):
		# numerator = 5, relative 15% -> denominator = 1 - 0.15 = 0.85.
		# min: 5 / (0.85 - 0.10) = 5 / 0.75 = 6.6667 -> 6.67
		# max: 5 / (0.85 - 0.20) = 5 / 0.65 = 7.6923 -> 7.69
		# Sanity: both exceed the absolute-only baseline (5/0.9=5.56, 5/0.8=6.25), proving the
		# relative cost shrank the denominator and pushed price up.
		result = calculate_prices([absolute(5), relative(15)], min_margin=0.10, max_margin=0.20)
		self.assertPriceEqual(result["min_price"], 6.67)
		self.assertPriceEqual(result["max_price"], 7.69)

	@change_settings("Navgold Settings", PINNED_SETTINGS)
	def test_vat_applied_as_vat_over_one_plus_vat(self):
		# numerator = 5, VAT 20% -> vat_rate = 0.20, vat_fraction = 0.20 / 1.20 = 0.166667.
		# denominator = 1 - 0.166667 = 0.833333.
		# min: 5 / (0.833333 - 0.10) = 5 / 0.733333 = 6.8182 -> 6.82
		# max: 5 / (0.833333 - 0.20) = 5 / 0.633333 = 7.8947 -> 7.89
		result = calculate_prices([absolute(5), vat(20)], min_margin=0.10, max_margin=0.20)
		self.assertPriceEqual(result["min_price"], 6.82)
		self.assertPriceEqual(result["max_price"], 7.89)

	@change_settings("Navgold Settings", PINNED_SETTINGS)
	def test_mixed_absolute_relative_vat(self):
		# numerator = 3 + 1 = 4.
		# denominator = 1 - 0.10 (relative) - 0.166667 (vat 20%) = 0.733333.
		# min: 4 / (0.733333 - 0.10) = 4 / 0.633333 = 6.3158 -> 6.32
		# max: 4 / (0.733333 - 0.20) = 4 / 0.533333 = 7.50
		result = calculate_prices(
			[absolute(3), absolute(1), relative(10), vat(20)],
			min_margin=0.10,
			max_margin=0.20,
		)
		self.assertPriceEqual(result["min_price"], 6.32)
		self.assertPriceEqual(result["max_price"], 7.50)

	def test_high_price_adjustment_kicks_in_above_threshold(self):
		# initial = 8.5 / (1 - 0.20) = 8.5 / 0.80 = 10.625 > 10 (threshold) -> re-price with the
		# high-price commission adjustment folded into the denominator:
		# 8.5 / (1 - 0.20 - 0.07) = 8.5 / 0.73 = 11.6438
		price = calculate_single_price(8.5, 1, 0.20, SETTINGS_ARG)
		self.assertAlmostEqual(price, 8.5 / 0.73, places=4)
		self.assertAlmostEqual(price, 11.6438, places=4)

	def test_no_adjustment_just_below_threshold(self):
		# 7 / (1 - 0.20) = 7 / 0.80 = 8.75, which is <= 10, so NO adjustment: stays 8.75.
		price = calculate_single_price(7, 1, 0.20, SETTINGS_ARG)
		self.assertAlmostEqual(price, 8.75, places=4)

	def test_no_adjustment_exactly_at_threshold(self):
		# 8 / (1 - 0.20) = 10.0 exactly. The guard is strictly ">", so at 10.0 there is NO adjustment.
		price = calculate_single_price(8, 1, 0.20, SETTINGS_ARG)
		self.assertAlmostEqual(price, 10.0, places=4)

	def test_degenerate_denominator_returns_zero(self):
		# denominator - margin = 1 - 1.0 = 0 -> must return 0, never divide-by-zero/inf.
		self.assertEqual(calculate_single_price(5, 1, 1.0, SETTINGS_ARG), 0)
		# denominator - margin = 0.5 - 0.6 = -0.1 (negative) -> must return 0, never a negative price.
		self.assertEqual(calculate_single_price(5, 0.5, 0.6, SETTINGS_ARG), 0)

	def test_degenerate_after_high_price_adjustment_returns_zero(self):
		# First stage: 100 / (1 - 0.95) = 100 / 0.05 = 2000 > 10 -> triggers adjustment.
		# Second stage denominator: 1 - 0.95 - 0.07 = -0.02 <= 0 -> must return 0, not a huge/negative price.
		self.assertEqual(calculate_single_price(100, 1, 0.95, SETTINGS_ARG), 0)

	@change_settings("Navgold Settings", PINNED_SETTINGS)
	def test_min_max_inversion_is_swapped(self):
		# Caller passes min_margin (0.40) > max_margin (0.10), which inverts the raw prices:
		#   price @ 0.40 = 5 / 0.60 = 8.3333  (larger)
		#   price @ 0.10 = 5 / 0.90 = 5.5556  (smaller)
		# calculate_prices must guarantee min_price <= max_price, so it swaps them.
		result = calculate_prices([absolute(5)], min_margin=0.40, max_margin=0.10)
		self.assertPriceEqual(result["min_price"], 5.56)
		self.assertPriceEqual(result["max_price"], 8.33)
		self.assertLessEqual(result["min_price"], result["max_price"])

	@change_settings("Navgold Settings", PINNED_SETTINGS)
	def test_reverse_margin_round_trips_absolute_only(self):
		# Forward: numerator 5, denominator 1, margin 0.20 -> price = 5 / 0.80 = 6.25 (< threshold).
		# Reverse from 6.25: vat=0, relative=0, no commission adjustment.
		#   net_revenue = 6.25, profit = 6.25 - 5 = 1.25, margin = 1.25 / 6.25 * 100 = 20.0
		forward_price = calculate_single_price(5, 1, 0.20, SETTINGS_ARG)
		reverse = calculate_margin_from_price([absolute(5)], forward_price)
		self.assertAlmostEqual(reverse["margin"], 20.0, places=2)
		self.assertAlmostEqual(reverse["profit"], 1.25, places=2)

	@change_settings("Navgold Settings", PINNED_SETTINGS)
	def test_reverse_margin_round_trips_mixed_stack(self):
		# Forward: numerator 5, denominator = 1 - 0.10 - 0.166667 = 0.733333, margin 0.20
		#   -> price = 5 / (0.733333 - 0.20) = 5 / 0.533333 = 9.375 (< threshold, no adjustment).
		# Reverse from 9.375:
		#   vat_amount      = 9.375 * 0.166667 = 1.5625
		#   relative_costs  = 9.375 * 0.10     = 0.9375
		#   net_revenue     = 9.375 - 1.5625 - 0.9375 = 6.875
		#   profit          = 6.875 - 5 = 1.875 -> rounds to 1.88
		#   margin          = 1.875 / 9.375 * 100 = 20.0
		denominator = 1 - 0.10 - (0.20 / 1.20)
		forward_price = calculate_single_price(5, denominator, 0.20, SETTINGS_ARG)
		reverse = calculate_margin_from_price([absolute(5), relative(10), vat(20)], forward_price)
		self.assertAlmostEqual(reverse["margin"], 20.0, places=2)
		self.assertAlmostEqual(reverse["profit"], 1.88, places=2)

	@change_settings("Navgold Settings", PINNED_SETTINGS)
	def test_reverse_margin_zero_for_non_positive_price(self):
		# Guard: a non-positive selling price can never yield a real margin; return zeros.
		self.assertEqual(calculate_margin_from_price([absolute(5)], 0), {"margin": 0, "profit": 0})
		self.assertEqual(calculate_margin_from_price([absolute(5)], -3), {"margin": 0, "profit": 0})
