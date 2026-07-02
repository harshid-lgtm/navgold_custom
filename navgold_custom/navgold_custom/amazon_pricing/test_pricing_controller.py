# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.tests.utils import change_settings

from navgold_custom.navgold_custom.amazon_pricing import price_calculator

# See test_strategy_controller.py for why these are pinned. Same documented fallbacks.
FIXED_SETTINGS = {"commission_threshold": 10, "high_price_adjustment": 0.07}

CTRL_COST_TYPE = "Test Ctrl Pricing Cost"
CTRL_ITEM_CODE = "TEST-CTRL-PRICING-ITEM"


def create_purchase_cost_type():
	if frappe.db.exists("Cost Type", CTRL_COST_TYPE):
		return CTRL_COST_TYPE

	cost_type = frappe.new_doc("Cost Type")
	cost_type.cost_type_name = CTRL_COST_TYPE
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


def create_strategy(proxy_sku, min_margin=10, max_margin=20, purchase_cost_value=5.0):
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
	strategy.insert()
	return strategy


def build_pricing(proxy_sku, is_active=1):
	pricing = frappe.new_doc("Amazon Product Pricing")
	pricing.proxy_sku = proxy_sku
	pricing.is_active = is_active
	return pricing


class TestPricingControllerStrategyOwnership(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_purchase_cost_type()
		create_test_item()
		price_calculator.get_cost_type_map.clear_cache()

	def test_strategy_from_other_proxy_sku_raises(self):
		owner_proxy_sku = create_proxy_sku()
		other_proxy_sku = create_proxy_sku()
		foreign_strategy = create_strategy(owner_proxy_sku)

		pricing = build_pricing(other_proxy_sku)
		pricing.fba_strategy = foreign_strategy.name

		with self.assertRaises(frappe.ValidationError):
			pricing.insert()

	def test_strategy_from_same_proxy_sku_passes(self):
		proxy_sku = create_proxy_sku()
		strategy = create_strategy(proxy_sku)

		pricing = build_pricing(proxy_sku)
		pricing.fba_strategy = strategy.name
		pricing.insert()

		self.assertTrue(frappe.db.exists("Amazon Product Pricing", pricing.name))


class TestPricingControllerCurrentPrices(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_purchase_cost_type()
		create_test_item()
		price_calculator.get_cost_type_map.clear_cache()

	def test_fixed_price_path_sets_min_equals_max(self):
		# fixed_price short-circuits update_current_prices: both bounds must equal the fixed value.
		proxy_sku = create_proxy_sku()
		pricing = build_pricing(proxy_sku)
		pricing.fixed_price = 1
		pricing.fixed_price_value = 12.34
		pricing.insert()

		self.assertEqual(pricing.current_min_price, 12.34)
		self.assertEqual(pricing.current_max_price, 12.34)

	def test_active_strategy_path_copies_calculated_band(self):
		# No FBA/SFP/backorder slots, so select_strategy falls through to the default strategy.
		# That strategy's band drives current prices. Absolute cost 5, margins 10%/20%:
		#   calculated_min_price = 5 / (1 - 0.10) = 5 / 0.90 = 5.5556 -> 5.56
		#   calculated_max_price = 5 / (1 - 0.20) = 5 / 0.80 = 6.25
		proxy_sku = create_proxy_sku()

		with patch.object(price_calculator, "get_pricing_settings", return_value=FIXED_SETTINGS):
			strategy = create_strategy(proxy_sku, purchase_cost_value=5.0)
			pricing = build_pricing(proxy_sku)
			pricing.default_strategy = strategy.name
			pricing.insert()

		# The default slot was selected as the active strategy.
		self.assertEqual(pricing.active_strategy, strategy.name)
		self.assertAlmostEqual(pricing.current_min_price, 5.56, places=2)
		self.assertAlmostEqual(pricing.current_max_price, 6.25, places=2)

	def test_no_strategy_yields_zero_band(self):
		# No slots at all -> active_strategy None -> current prices zeroed.
		proxy_sku = create_proxy_sku()
		pricing = build_pricing(proxy_sku)
		pricing.insert()

		self.assertIsNone(pricing.active_strategy)
		self.assertEqual(pricing.current_min_price, 0)
		self.assertEqual(pricing.current_max_price, 0)


class TestPricingControllerSubmitGating(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_purchase_cost_type()
		create_test_item()
		price_calculator.get_cost_type_map.clear_cache()

	@change_settings("Navgold Settings", {"enable_pricing_sync": 0})
	def test_submit_blocked_when_sync_disabled(self):
		proxy_sku = create_proxy_sku()
		pricing = build_pricing(proxy_sku)
		pricing.insert()

		with self.assertRaises(frappe.ValidationError):
			pricing.submit_to_amazon()

	@change_settings("Navgold Settings", {"enable_pricing_sync": 1})
	def test_submit_blocked_for_inactive_product(self):
		# Sync is enabled, so the guard reaches the is_active check and throws for the inactive doc.
		proxy_sku = create_proxy_sku()
		pricing = build_pricing(proxy_sku, is_active=0)
		pricing.insert()

		with self.assertRaises(frappe.ValidationError):
			pricing.submit_to_amazon()

	@change_settings(
		"Navgold Settings",
		{"enable_pricing_sync": 1, "amazon_publishing_mode": "Stock and Prices"},
	)
	def test_submit_blocked_without_calculated_prices_in_stock_and_prices(self):
		# Active product with no strategy -> current prices are 0. In "Stock and Prices" mode the
		# missing-price precondition must block the submit before any feed is built.
		proxy_sku = create_proxy_sku()
		pricing = build_pricing(proxy_sku, is_active=1)
		pricing.insert()

		self.assertEqual(pricing.current_min_price, 0)

		with self.assertRaises(frappe.ValidationError):
			pricing.submit_to_amazon()
