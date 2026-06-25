# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.shipping_cost import get_shipping_cost

SHIPPING_COST_TYPE = "Test Shipping Weight Cost"
SHIPPING_ITEM_CODE = "TEST-SHIPPING-COST-ITEM"

# (upper_weight_g, shipping_cost) — the live UK bands.
TIERS = [(2000, 3), (7000, 4), (15000, 5), (20000, 6)]
PACKAGING_BUFFER = 200


def set_shipping_settings(tiers=TIERS, buffer=PACKAGING_BUFFER):
	settings = frappe.get_doc("Navgold Settings")
	settings.shipping_packaging_buffer_g = buffer
	settings.set(
		"shipping_weight_tiers",
		[{"upper_weight_g": upper, "shipping_cost": cost} for upper, cost in tiers],
	)
	settings.save()
	frappe.clear_document_cache("Navgold Settings", "Navgold Settings")


def create_shipping_cost_type():
	if not frappe.db.exists("Cost Type", SHIPPING_COST_TYPE):
		cost_type = frappe.new_doc("Cost Type")
		cost_type.cost_type_name = SHIPPING_COST_TYPE
		cost_type.calculation_type = "Absolute"
		cost_type.insert()
	return SHIPPING_COST_TYPE


def create_item(item_code):
	if frappe.db.exists("Item", item_code):
		return item_code
	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = item_code
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 0
	item.insert()
	return item.name


def create_proxy_sku(item_code, shipping_weight_g):
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.item = item_code
	proxy_sku.uom = "Nos"
	proxy_sku.is_active = 1
	proxy_sku.shipping_weight_g = shipping_weight_g
	proxy_sku.insert()
	return proxy_sku.name


def create_shipping_strategy(proxy_sku, strategy_name="Shipping-Test"):
	strategy = frappe.new_doc("Amazon Product Strategy")
	strategy.proxy_sku = proxy_sku
	strategy.strategy_name = strategy_name
	strategy.is_active = 1
	strategy.min_margin = 5
	strategy.max_margin = 10
	strategy.append(
		"costs",
		{
			"cost_type": SHIPPING_COST_TYPE,
			"source": "Shipping-Sync",
			"rank": 1,
			# Bogus value that populate_costs must overwrite from the tier table.
			"value": 999,
		},
	)
	strategy.insert()
	return strategy


def shipping_value(strategy):
	return next(cost for cost in strategy.costs if cost.source == "Shipping-Sync").value


class TestShippingCost(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_shipping_cost_type()
		cls.item = create_item(SHIPPING_ITEM_CODE)

	def setUp(self):
		set_shipping_settings()

	def test_buffer_pushes_weight_into_next_band(self):
		# 1800g + 200g buffer = 2000g -> still the <=2000 band (£3).
		self.assertEqual(get_shipping_cost(1800), 3)
		# 1801g + 200g = 2001g -> crosses into the next band (£4).
		self.assertEqual(get_shipping_cost(1801), 4)

	def test_each_band_boundary(self):
		self.assertEqual(get_shipping_cost(6800), 4)  # eff 7000
		self.assertEqual(get_shipping_cost(6801), 5)  # eff 7001
		self.assertEqual(get_shipping_cost(14800), 5)  # eff 15000
		self.assertEqual(get_shipping_cost(14801), 6)  # eff 15001
		self.assertEqual(get_shipping_cost(19800), 6)  # eff 20000

	def test_above_top_tier_bills_top_band(self):
		# Anything heavier than the last tier falls through to the top band (£6).
		self.assertEqual(get_shipping_cost(50000), 6)

	def test_buffer_is_configurable(self):
		set_shipping_settings(buffer=0)
		self.assertEqual(get_shipping_cost(2000), 3)  # eff 2000
		self.assertEqual(get_shipping_cost(2001), 4)  # eff 2001

	def test_missing_weight_throws(self):
		self.assertRaises(frappe.ValidationError, get_shipping_cost, 0)
		self.assertRaises(frappe.ValidationError, get_shipping_cost, None)

	def test_unconfigured_tiers_throw(self):
		set_shipping_settings(tiers=[])
		self.assertRaises(frappe.ValidationError, get_shipping_cost, 1000)

	def test_populate_costs_sets_tier_value_for_merchant_strategy(self):
		proxy_sku = create_proxy_sku(self.item, shipping_weight_g=6801)
		strategy = create_shipping_strategy(proxy_sku)
		self.assertEqual(shipping_value(strategy), 5)  # eff 7001 -> £5

	def test_fba_strategy_is_excluded(self):
		proxy_sku = create_proxy_sku(self.item, shipping_weight_g=6801)
		strategy = create_shipping_strategy(proxy_sku, strategy_name="FBA")

		# Before the pricing links this strategy as the FBA slot, it is treated as merchant.
		self.assertEqual(shipping_value(strategy), 5)

		pricing = frappe.new_doc("Amazon Product Pricing")
		pricing.proxy_sku = proxy_sku
		pricing.fba_strategy = strategy.name
		pricing.insert()

		strategy.save()
		self.assertEqual(shipping_value(strategy), 0)
