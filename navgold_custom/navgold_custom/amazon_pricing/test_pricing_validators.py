# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.tests.utils import change_settings

from navgold_custom.navgold_custom.amazon_pricing import amazon_feed_builder
from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
	build_pricing_feed,
	get_sku_inventory_data,
)
from navgold_custom.navgold_custom.amazon_pricing.pricing_validators import (
	collect_pricing_violations,
	format_blocked_records,
)

PURCHASE_COST_TYPE = "Test Purchase Cost"
TEST_ITEM_CODE = "TEST-PRICING-VALIDATOR-ITEM"


def create_purchase_cost_type():
	if frappe.db.exists("Cost Type", PURCHASE_COST_TYPE):
		return PURCHASE_COST_TYPE

	cost_type = frappe.new_doc("Cost Type")
	cost_type.cost_type_name = PURCHASE_COST_TYPE
	cost_type.is_absolute = 1
	cost_type.is_purchase_cost = 1
	cost_type.insert()
	return cost_type.name


def create_test_item():
	if frappe.db.exists("Item", TEST_ITEM_CODE):
		return TEST_ITEM_CODE

	item = frappe.new_doc("Item")
	item.item_code = TEST_ITEM_CODE
	item.item_name = TEST_ITEM_CODE
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 1
	item.insert()
	return item.name


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


def create_strategy(proxy_sku, purchase_cost_value=5.0, cost_source="Manual", is_active=1):
	strategy = frappe.new_doc("Amazon Product Strategy")
	strategy.proxy_sku = proxy_sku
	strategy.strategy_name = "default"
	strategy.is_active = is_active
	strategy.min_margin = 10
	strategy.max_margin = 20
	strategy.append(
		"costs",
		{
			"cost_type": PURCHASE_COST_TYPE,
			"value": purchase_cost_value,
			"source": cost_source,
			"rank": 1,
			"is_active": 1,
		},
	)
	strategy.insert()
	return strategy


def create_pricing(proxy_sku, strategy=None, fixed_price_value=None):
	pricing = frappe.new_doc("Amazon Product Pricing")
	pricing.proxy_sku = proxy_sku
	pricing.is_active = 1
	if strategy:
		pricing.default_strategy = strategy.name
	if fixed_price_value is not None:
		pricing.fixed_price = 1
		pricing.fixed_price_value = fixed_price_value
	pricing.insert()
	return pricing.name


class TestPricingValidators(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_purchase_cost_type()
		create_test_item()

	def test_valid_pricing_passes(self):
		proxy_sku = create_proxy_sku()
		strategy = create_strategy(proxy_sku)
		pricing_name = create_pricing(proxy_sku, strategy)

		violations = collect_pricing_violations([pricing_name], include_deviation=False)

		self.assertNotIn(pricing_name, violations)

	def test_no_active_strategy_flagged(self):
		proxy_sku = create_proxy_sku()
		pricing_name = create_pricing(proxy_sku)

		violations = collect_pricing_violations([pricing_name], include_deviation=False)

		self.assertIn("No active strategy", violations[pricing_name])

	def test_missing_strategy_flagged(self):
		proxy_sku = create_proxy_sku()
		strategy = create_strategy(proxy_sku)
		pricing_name = create_pricing(proxy_sku, strategy)
		frappe.db.set_value(
			"Amazon Product Pricing", pricing_name, "active_strategy", "GHOST-STRATEGY", update_modified=False
		)

		violations = collect_pricing_violations([pricing_name], include_deviation=False)

		self.assertIn("Active strategy GHOST-STRATEGY does not exist", violations[pricing_name])

	def test_disabled_strategy_flagged(self):
		proxy_sku = create_proxy_sku()
		strategy = create_strategy(proxy_sku)
		pricing_name = create_pricing(proxy_sku, strategy)
		frappe.db.set_value("Amazon Product Strategy", strategy.name, "is_active", 0, update_modified=False)

		violations = collect_pricing_violations([pricing_name], include_deviation=False)

		self.assertIn(f"Active strategy {strategy.name} is disabled", violations[pricing_name])

	def test_zero_purchase_cost_flagged(self):
		proxy_sku = create_proxy_sku()
		strategy = create_strategy(proxy_sku, purchase_cost_value=0)
		pricing_name = create_pricing(proxy_sku, strategy)

		violations = collect_pricing_violations([pricing_name], include_deviation=False)

		self.assertTrue(
			any(reason.startswith("Purchase cost is 0") for reason in violations[pricing_name]),
			violations[pricing_name],
		)

	def test_po_sync_without_history_resolves_to_zero_and_is_flagged(self):
		# PO-Sync with no purchase history resolves to flt(None) == 0, so prices compute from a zero cost.
		proxy_sku = create_proxy_sku()
		strategy = create_strategy(proxy_sku, purchase_cost_value=999, cost_source="PO-Sync")
		pricing_name = create_pricing(proxy_sku, strategy)

		self.assertEqual(
			frappe.db.get_value("Amazon Product Cost", {"parent": strategy.name, "rank": 1}, "value"),
			0,
		)

		violations = collect_pricing_violations([pricing_name], include_deviation=False)

		self.assertTrue(
			any("Purchase cost is 0" in reason for reason in violations[pricing_name]),
			violations[pricing_name],
		)

	def test_min_above_max_flagged(self):
		proxy_sku = create_proxy_sku()
		strategy = create_strategy(proxy_sku)
		pricing_name = create_pricing(proxy_sku, strategy)
		frappe.db.set_value(
			"Amazon Product Pricing",
			pricing_name,
			{"current_min_price": 10, "current_max_price": 5},
			update_modified=False,
		)

		violations = collect_pricing_violations([pricing_name], include_deviation=False)

		self.assertIn("Min price 10 is above max price 5", violations[pricing_name])

	def test_min_equals_max_without_fixed_price_flagged(self):
		proxy_sku = create_proxy_sku()
		strategy = create_strategy(proxy_sku)
		pricing_name = create_pricing(proxy_sku, strategy)
		frappe.db.set_value(
			"Amazon Product Pricing",
			pricing_name,
			{"current_min_price": 10, "current_max_price": 10},
			update_modified=False,
		)

		violations = collect_pricing_violations([pricing_name], include_deviation=False)

		self.assertIn("Min price equals max price without a fixed price", violations[pricing_name])

	def test_fixed_price_min_equals_max_allowed(self):
		proxy_sku = create_proxy_sku()
		strategy = create_strategy(proxy_sku)
		pricing_name = create_pricing(proxy_sku, strategy, fixed_price_value=10)

		violations = collect_pricing_violations([pricing_name], include_deviation=False)

		self.assertNotIn(pricing_name, violations)

	def test_deviation_reasons_included_when_requested(self):
		proxy_sku = create_proxy_sku()
		strategy = create_strategy(proxy_sku)
		pricing_name = create_pricing(proxy_sku, strategy)

		violations = collect_pricing_violations([pricing_name], include_deviation=True)

		self.assertIn("No current selling price", violations[pricing_name])

	def test_empty_input_returns_empty(self):
		self.assertEqual(collect_pricing_violations([]), {})

	def test_format_blocked_records(self):
		blocked_reasons = {"SKU-B": ["reason2"], "SKU-A": ["reason1", "reason3"]}

		self.assertEqual(
			format_blocked_records(blocked_reasons),
			"SKU-A: reason1; reason3\nSKU-B: reason2",
		)


class TestFeedBuilderPublishingMode(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_purchase_cost_type()
		create_test_item()

	def get_patch_paths(self, feed_result):
		feed = frappe.parse_json(feed_result["feed_content"])
		return feed["messages"], [
			patch_entry["path"] for message in feed["messages"] for patch_entry in message["patches"]
		]

	def test_stock_only_mode_omits_price_patches(self):
		proxy_sku = create_proxy_sku()
		strategy = create_strategy(proxy_sku)
		pricing_name = create_pricing(proxy_sku, strategy)

		with change_settings(
			"Navgold Settings",
			{
				"amazon_publishing_mode": "Stock Only",
				# the dev/restored site runs Publish Selected Prices Only, which would
				# filter the fixture out of the feed before any mode logic runs
				"pricing_publishing_mode": "Publish All Prices",
			},
		):
			feed_result = build_pricing_feed([pricing_name], "SELLER", "MARKETPLACE")

		messages, patch_paths = self.get_patch_paths(feed_result)
		self.assertEqual(len(messages), 1)
		self.assertNotIn("/attributes/purchasable_offer", patch_paths)
		self.assertIn("/attributes/fulfillment_availability", patch_paths)
		self.assertEqual(feed_result["publishing_mode"], "Stock Only")
		self.assertEqual(feed_result["blocked_validation"], [])
		self.assertEqual(feed_result["blocked_price_deviation"], [])

	def test_stock_and_prices_mode_includes_price_patches(self):
		proxy_sku = create_proxy_sku(selling_price=6)
		strategy = create_strategy(proxy_sku)
		pricing_name = create_pricing(proxy_sku, strategy)

		with change_settings(
			"Navgold Settings",
			{
				"amazon_publishing_mode": "Stock and Prices",
				"pricing_publishing_mode": "Publish All Prices",
				"price_change_threshold_percent": 50,
				"price_change_block_direction": "Both",
			},
		):
			feed_result = build_pricing_feed([pricing_name], "SELLER", "MARKETPLACE")

		messages, patch_paths = self.get_patch_paths(feed_result)
		self.assertEqual(len(messages), 1)
		self.assertIn("/attributes/purchasable_offer", patch_paths)
		self.assertIn("/attributes/fulfillment_availability", patch_paths)
		self.assertEqual(feed_result["blocked_validation"], [])

	def test_invalid_pricing_blocked_from_price_feed(self):
		proxy_sku = create_proxy_sku(selling_price=6)
		strategy = create_strategy(proxy_sku)
		pricing_name = create_pricing(proxy_sku, strategy)
		# min == max without fixed price: passes the deviation guard, fails validation.
		frappe.db.set_value(
			"Amazon Product Pricing",
			pricing_name,
			{"current_min_price": 6, "current_max_price": 6},
			update_modified=False,
		)

		with change_settings(
			"Navgold Settings",
			{
				"amazon_publishing_mode": "Stock and Prices",
				"pricing_publishing_mode": "Publish All Prices",
				"price_change_threshold_percent": 50,
				"price_change_block_direction": "Both",
			},
		):
			feed_result = build_pricing_feed([pricing_name], "SELLER", "MARKETPLACE")

		messages, _patch_paths = self.get_patch_paths(feed_result)
		self.assertEqual(messages, [])
		self.assertEqual(len(feed_result["blocked_validation"]), 1)
		self.assertEqual(feed_result["blocked_validation"][0]["proxy_sku"], proxy_sku)
		self.assertIn(
			"Min price equals max price without a fixed price",
			feed_result["blocked_validation"][0]["reasons"],
		)


class TestBundleZeroStock(IntegrationTestCase):
	def build_inventory_data(self, mfn_stock, bundle_buildable_map, pack_size=1.0):
		pricing = frappe._dict(
			proxy_sku="SKU-BUNDLE",
			active_strategy="STRAT-default",
			fba_strategy=None,
			sfp_strategy=None,
			backorder_strategy=None,
		)
		stock_info_map = {
			"SKU-BUNDLE": {
				"mfn_stock": mfn_stock,
				"fba_stock": 0,
				"has_restock_date": False,
				"restock_date": None,
			}
		}
		proxy_sku_info = {
			"SKU-BUNDLE": {
				"item": "BUNDLE-ITEM",
				"uom": "Nos",
				"pool_size": 1,
				"pack_size": pack_size,
				"merchant_shipping_group": None,
			}
		}
		return get_sku_inventory_data(
			pricing,
			None,
			proxy_sku_info,
			stock_info_map,
			supplier_data_map={},
			lead_time_map={},
			bundle_buildable_map=bundle_buildable_map,
		)

	def test_bundle_with_insufficient_component_stock_pushes_zero(self):
		inventory_data = self.build_inventory_data(mfn_stock=10, bundle_buildable_map={"BUNDLE-ITEM": 0})

		self.assertEqual(inventory_data["quantity"], 0)

	def test_bundle_with_partial_component_stock_pushes_zero(self):
		inventory_data = self.build_inventory_data(mfn_stock=10, bundle_buildable_map={"BUNDLE-ITEM": 5})

		self.assertEqual(inventory_data["quantity"], 0)

	def test_bundle_with_sufficient_component_stock_keeps_quantity(self):
		inventory_data = self.build_inventory_data(mfn_stock=10, bundle_buildable_map={"BUNDLE-ITEM": 10})

		self.assertEqual(inventory_data["quantity"], 10)

	def test_non_bundle_item_is_unaffected(self):
		inventory_data = self.build_inventory_data(mfn_stock=10, bundle_buildable_map={})

		self.assertEqual(inventory_data["quantity"], 10)

	def test_bundle_buildable_map_constrained_by_scarcest_component(self):
		with (
			patch.object(
				amazon_feed_builder,
				"get_bundle_components",
				return_value={"BUNDLE-ITEM": [{"item_code": "C1", "qty": 2}, {"item_code": "C2", "qty": 1}]},
			),
			patch.object(
				amazon_feed_builder,
				"get_free_qty_map",
				return_value={"C1": {"free_qty": 3}, "C2": {"free_qty": 100}},
			),
		):
			buildable_map = amazon_feed_builder.get_bundle_buildable_map(["BUNDLE-ITEM"])

		self.assertEqual(buildable_map["BUNDLE-ITEM"], 1)
