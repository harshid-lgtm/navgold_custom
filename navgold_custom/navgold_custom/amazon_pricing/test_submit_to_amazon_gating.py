# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, change_settings
from frappe.utils.data import flt

from navgold_custom.navgold_custom.amazon_pricing import price_calculator
from navgold_custom.navgold_custom.doctype.amazon_product_pricing import amazon_product_pricing

PURCHASE_COST_TYPE = "Test Controller Purchase Cost"
TEST_ITEM_CODE = "TEST-PRICING-CONTROLLER-ITEM"


def create_purchase_cost_type():
	if not frappe.db.exists("Cost Type", PURCHASE_COST_TYPE):
		cost_type = frappe.new_doc("Cost Type")
		cost_type.cost_type_name = PURCHASE_COST_TYPE
		cost_type.is_group = 1
		cost_type.calculation_type = "Absolute"
		cost_type.is_purchase_cost = 1
		cost_type.insert()
	# get_cost_type_map is redis-cached; a stale map would drop the just-created type.
	price_calculator.get_cost_type_map.clear_cache()
	return PURCHASE_COST_TYPE


def create_test_item():
	if not frappe.db.exists("Item", TEST_ITEM_CODE):
		item = frappe.new_doc("Item")
		item.item_code = TEST_ITEM_CODE
		item.item_name = TEST_ITEM_CODE
		item.item_group = "All Item Groups"
		item.stock_uom = "Nos"
		item.is_stock_item = 1
		item.insert()
	return TEST_ITEM_CODE


def create_proxy_sku(selling_price=None):
	# asin left unset so Proxy SKU.validate's duplicate-active check never trips.
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.is_active = 1
	proxy_sku.item = TEST_ITEM_CODE
	proxy_sku.uom = "Nos"
	if selling_price is not None:
		proxy_sku.current_selling_price = selling_price
	proxy_sku.insert()
	return proxy_sku.name


def create_strategy(proxy_sku, purchase_cost_value=5.0, min_margin=10, max_margin=20):
	strategy = frappe.new_doc("Amazon Product Strategy")
	strategy.proxy_sku = proxy_sku
	strategy.strategy_name = "default"
	strategy.is_active = 1
	strategy.min_margin = min_margin
	strategy.max_margin = max_margin
	strategy.append(
		"costs",
		{
			"cost_type": PURCHASE_COST_TYPE,
			"value": purchase_cost_value,
			"value_mode": "Manual",
			"rank": 1,
			"is_active": 1,
		},
	)
	strategy.insert()
	return strategy


def create_pricing(proxy_sku, strategy=None):
	pricing = frappe.new_doc("Amazon Product Pricing")
	pricing.proxy_sku = proxy_sku
	pricing.is_active = 1
	if strategy:
		pricing.default_strategy = strategy.name
	pricing.insert()
	return pricing.name


class TestSubmitToAmazonGating(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_purchase_cost_type()
		create_test_item()

	def test_stock_only_mode_submits_despite_zero_price(self):
		# Stock Only feeds carry no price patches, so the zero-price precondition must NOT block them.
		proxy_sku = create_proxy_sku()
		pricing_name = create_pricing(proxy_sku)  # no strategy -> current prices are 0
		pricing = frappe.get_doc("Amazon Product Pricing", pricing_name)
		self.assertFalse(pricing.current_min_price)
		self.assertFalse(pricing.current_max_price)

		with change_settings(
			"Navgold Settings",
			{"enable_pricing_sync": 1, "amazon_publishing_mode": "Stock Only"},
		):
			frappe.clear_document_cache("Navgold Settings", "Navgold Settings")
			with patch.object(amazon_product_pricing, "submit_pricing") as mock_submit:
				pricing.submit_to_amazon()

		# Reaches the submit path (no throw) even with zero calculated price.
		mock_submit.assert_called_once_with([pricing_name])

	def test_valid_push_calls_submit_pricing(self):
		# All guards pass: sync enabled, active, Stock and Prices, calculated prices present and valid.
		proxy_sku = create_proxy_sku(selling_price=6)
		strategy = create_strategy(proxy_sku, purchase_cost_value=5)
		pricing_name = create_pricing(proxy_sku, strategy)

		# Fixture band (cost 5, margins 10/20): min 5.56, max 6.25 (both < commission_threshold 10).
		pricing = frappe.get_doc("Amazon Product Pricing", pricing_name)
		self.assertAlmostEqual(flt(pricing.current_min_price, 2), 5.56, places=2)
		self.assertAlmostEqual(flt(pricing.current_max_price, 2), 6.25, places=2)

		with change_settings(
			"Navgold Settings",
			{
				"enable_pricing_sync": 1,
				"amazon_publishing_mode": "Stock and Prices",
				"price_change_threshold_percent": 50,
				"price_change_block_direction": "Both",
			},
		):
			frappe.clear_document_cache("Navgold Settings", "Navgold Settings")
			with patch.object(amazon_product_pricing, "submit_pricing") as mock_submit:
				pricing.submit_to_amazon()

		mock_submit.assert_called_once_with([pricing_name])


class TestHighPriceCommissionBand(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_purchase_cost_type()
		create_test_item()

	def test_high_price_band_applies_commission_adjustment(self):
		# Cost stack drives the band ABOVE commission_threshold (10), so the >threshold re-price
		# branch must fire through the real Strategy controller -> price_calculator wiring.
		# Hand-derived: numerator = 20.00 (Absolute), denominator = 1.00 (no VAT/relative).
		#   min: initial = 20 / (1 - 0.10) = 22.22 > 10 -> 20 / (1 - 0.10 - 0.07) = 20 / 0.83 = 24.10
		#   max: initial = 20 / (1 - 0.20) = 25.00 > 10 -> 20 / (1 - 0.20 - 0.07) = 20 / 0.73 = 27.40
		proxy_sku = create_proxy_sku()

		with change_settings(
			"Navgold Settings",
			{"commission_threshold": 10, "high_price_commission_adjustment": 0.07},
		):
			frappe.clear_document_cache("Navgold Settings", "Navgold Settings")
			price_calculator.get_cost_type_map.clear_cache()
			strategy = create_strategy(proxy_sku, purchase_cost_value=20, min_margin=10, max_margin=20)

		strategy.reload()
		self.assertAlmostEqual(flt(strategy.calculated_min_price, 2), 24.10, places=2)
		self.assertAlmostEqual(flt(strategy.calculated_max_price, 2), 27.40, places=2)
