# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing import price_calculator

# Pin the Navgold Settings-derived pricing knobs so the band is decided by the formula, not by
# whatever the restored dev site has configured. commission_threshold=10 / high_price_adjustment=0.07
# are the documented get_pricing_settings() fallbacks, matching test_price_calculator.py.
FIXED_SETTINGS = {"commission_threshold": 10, "high_price_adjustment": 0.07}

CTRL_COST_TYPE = "Test Ctrl Purchase Cost"
CTRL_ITEM_CODE = "TEST-CTRL-STRATEGY-ITEM"


def create_purchase_cost_type():
	if frappe.db.exists("Cost Type", CTRL_COST_TYPE):
		return CTRL_COST_TYPE

	cost_type = frappe.new_doc("Cost Type")
	cost_type.cost_type_name = CTRL_COST_TYPE
	cost_type.is_group = 1
	cost_type.calculation_type = "Absolute"
	cost_type.is_purchase_cost = 1
	cost_type.insert()
	return cost_type.name


def create_test_item():
	if frappe.db.exists("Item", CTRL_ITEM_CODE):
		return CTRL_ITEM_CODE

	item = frappe.new_doc("Item")
	item.item_code = CTRL_ITEM_CODE
	item.item_name = CTRL_ITEM_CODE
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 1
	item.insert()
	return item.name


def create_proxy_sku():
	# asin left unset so Proxy SKU.validate's duplicate-active check never trips.
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.is_active = 1
	proxy_sku.item = CTRL_ITEM_CODE
	proxy_sku.uom = "Nos"
	proxy_sku.insert()
	return proxy_sku.name


def build_strategy(proxy_sku, min_margin=10, max_margin=20, purchase_cost_value=5.0):
	strategy = frappe.new_doc("Amazon Product Strategy")
	strategy.proxy_sku = proxy_sku
	strategy.strategy_name = "default"
	strategy.is_active = 1
	strategy.min_margin = min_margin
	strategy.max_margin = max_margin
	strategy.append(
		"costs",
		{
			"cost_type": CTRL_COST_TYPE,
			"value": purchase_cost_value,
			"value_mode": "Manual",
			"rank": 1,
			"is_active": 1,
		},
	)
	return strategy


class TestStrategyControllerMargin(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_purchase_cost_type()
		create_test_item()
		# get_cost_type_map is redis_cached; drop stale entries so our freshly-created Cost Type
		# resolves to calculation_type "Absolute" during price calculation.
		price_calculator.get_cost_type_map.clear_cache()

	def test_min_margin_above_max_margin_raises(self):
		proxy_sku = create_proxy_sku()
		strategy = build_strategy(proxy_sku, min_margin=30, max_margin=20)

		with self.assertRaises(frappe.ValidationError):
			strategy.insert()

	def test_min_margin_equal_max_margin_inserts(self):
		proxy_sku = create_proxy_sku()
		strategy = build_strategy(proxy_sku, min_margin=20, max_margin=20)

		strategy.insert()

		self.assertTrue(frappe.db.exists("Amazon Product Strategy", strategy.name))

	def test_min_margin_below_max_margin_inserts(self):
		proxy_sku = create_proxy_sku()
		strategy = build_strategy(proxy_sku, min_margin=10, max_margin=20)

		strategy.insert()

		self.assertTrue(frappe.db.exists("Amazon Product Strategy", strategy.name))


class TestStrategyControllerCalculatedBand(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_purchase_cost_type()
		create_test_item()
		price_calculator.get_cost_type_map.clear_cache()

	def test_calculated_band_from_single_absolute_cost(self):
		# One Absolute purchase cost of 5, margins 10%/20%. numerator = 5, denominator = 1.
		#   calculated_min_price = 5 / (1 - 0.10) = 5 / 0.90 = 5.5556 -> 5.56
		#   calculated_max_price = 5 / (1 - 0.20) = 5 / 0.80 = 6.25
		# Both < commission_threshold 10, so no high-price adjustment applies.
		proxy_sku = create_proxy_sku()
		strategy = build_strategy(proxy_sku, min_margin=10, max_margin=20, purchase_cost_value=5.0)

		with patch.object(price_calculator, "get_pricing_settings", return_value=FIXED_SETTINGS):
			strategy.insert()

		self.assertAlmostEqual(strategy.calculated_min_price, 5.56, places=2)
		self.assertAlmostEqual(strategy.calculated_max_price, 6.25, places=2)

	def test_calculated_band_scales_with_cost(self):
		# Absolute cost of 8, margins 10%/20%. numerator = 8, denominator = 1.
		#   min = 8 / 0.90 = 8.8889 -> 8.89
		#   max = 8 / 0.80 = 10.00 exactly. The high-price guard is strictly ">", so at 10.0 there
		#   is NO adjustment; the band top stays 10.00.
		proxy_sku = create_proxy_sku()
		strategy = build_strategy(proxy_sku, min_margin=10, max_margin=20, purchase_cost_value=8.0)

		with patch.object(price_calculator, "get_pricing_settings", return_value=FIXED_SETTINGS):
			strategy.insert()

		self.assertAlmostEqual(strategy.calculated_min_price, 8.89, places=2)
		self.assertAlmostEqual(strategy.calculated_max_price, 10.00, places=2)
