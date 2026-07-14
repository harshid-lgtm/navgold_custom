# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.tests.utils import change_settings

from navgold_custom.navgold_custom.amazon_pricing import amazon_feed_builder, purchase_cost_sources
from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
	SHIPPING_GROUPS,
	build_inventory_patches,
	build_pricing_feed,
	get_sku_inventory_data,
)
from navgold_custom.navgold_custom.amazon_pricing.pricing_validators import (
	collect_pricing_violations,
	format_blocked_records,
)

PURCHASE_COST_TYPE = "Test Purchase Cost"
TEST_ITEM_CODE = "TEST-PRICING-VALIDATOR-ITEM"


def create_purchase_cost_type(name=PURCHASE_COST_TYPE, allowed_zero_valued=0):
	if frappe.db.exists("Cost Type", name):
		return name

	cost_type = frappe.new_doc("Cost Type")
	cost_type.cost_type_name = name
	cost_type.is_group = 1
	cost_type.calculation_type = "Absolute"
	cost_type.is_purchase_cost = 1
	cost_type.allowed_zero_valued = allowed_zero_valued
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


def ensure_purchase_sub_type(source):
	# Manual rows need no sub-type policy; a fed row's source lives on its cost sub type (tree leaf) now.
	sub_type_name = f"{PURCHASE_COST_TYPE}-{source}"
	if not frappe.db.exists("Cost Type", sub_type_name):
		sub_type = frappe.new_doc("Cost Type")
		sub_type.is_group = 0
		sub_type.parent_cost_type = PURCHASE_COST_TYPE
		sub_type.cost_type_name = source
		sub_type.source = source
		sub_type.insert()
	return sub_type_name


def create_strategy(
	proxy_sku, purchase_cost_value=5.0, cost_source="Manual", is_active=1, cost_type=PURCHASE_COST_TYPE
):
	strategy = frappe.new_doc("Amazon Product Strategy")
	strategy.proxy_sku = proxy_sku
	strategy.strategy_name = "default"
	strategy.is_active = is_active
	strategy.min_margin = 10
	strategy.max_margin = 20

	row = {
		"cost_type": cost_type,
		"value": purchase_cost_value,
		"rank": 1,
		"is_active": 1,
	}
	if cost_source == "Manual":
		row["value_mode"] = "Manual"
	else:
		row["value_mode"] = "Auto"
		row["cost_sub_type"] = ensure_purchase_sub_type(cost_source)
	strategy.append("costs", row)
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

	def test_allowed_zero_valued_purchase_cost_not_flagged(self):
		cost_type = create_purchase_cost_type(f"{PURCHASE_COST_TYPE} Zero Allowed", allowed_zero_valued=1)
		proxy_sku = create_proxy_sku()
		strategy = create_strategy(proxy_sku, purchase_cost_value=0, cost_type=cost_type)
		pricing_name = create_pricing(proxy_sku, strategy)

		violations = collect_pricing_violations([pricing_name], include_deviation=False)

		self.assertFalse(
			[reason for reason in violations.get(pricing_name, []) if reason.startswith("Purchase cost is")],
			violations.get(pricing_name),
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

	def test_min_at_or_below_cost_flagged(self):
		proxy_sku = create_proxy_sku()
		strategy = create_strategy(proxy_sku, purchase_cost_value=10)
		pricing_name = create_pricing(proxy_sku, strategy)
		frappe.db.set_value(
			"Amazon Product Pricing",
			pricing_name,
			{"current_min_price": 10, "current_max_price": 20},
			update_modified=False,
		)

		violations = collect_pricing_violations([pricing_name], include_deviation=False)

		self.assertIn("Min price 10 is at or below purchase cost 10", violations[pricing_name])

	def test_min_above_cost_passes_floor(self):
		proxy_sku = create_proxy_sku()
		strategy = create_strategy(proxy_sku, purchase_cost_value=5)
		pricing_name = create_pricing(proxy_sku, strategy)
		frappe.db.set_value(
			"Amazon Product Pricing",
			pricing_name,
			{"current_min_price": 8, "current_max_price": 20},
			update_modified=False,
		)

		violations = collect_pricing_violations([pricing_name], include_deviation=False)

		self.assertNotIn(pricing_name, violations)

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

	def get_single_offer(self, feed_result):
		feed = frappe.parse_json(feed_result["feed_content"])
		offers = [
			patch_entry["value"][0]
			for message in feed["messages"]
			for patch_entry in message["patches"]
			if patch_entry["path"] == "/attributes/purchasable_offer"
		]
		self.assertEqual(len(offers), 1)
		return feed, offers[0]

	@staticmethod
	def offer_value(offer, key):
		# purchasable_offer nests each price as key -> [ { "schedule": [ { "value_with_tax": X } ] } ]
		return offer[key][0]["schedule"][0]["value_with_tax"]

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

		# Hand-derived from the fixture: purchase_cost=5.00 (Absolute), min_margin=10%, max_margin=20%.
		# price = numerator / (denominator - margin); numerator=5.00, denominator=1.00 (no VAT/relative).
		# min = 5.00 / (1.00 - 0.10) = 5.5556 -> 5.56 ; max = 5.00 / (1.00 - 0.20) = 6.25
		# Both < commission_threshold (10) so no high-price adjustment.
		_feed, offer = self.get_single_offer(feed_result)
		self.assertAlmostEqual(self.offer_value(offer, "minimum_seller_allowed_price"), 5.56, places=2)
		self.assertAlmostEqual(self.offer_value(offer, "maximum_seller_allowed_price"), 6.25, places=2)

	def test_price_feed_always_includes_min_floor(self):
		# Even with send_min_price_to_amazon OFF, the min floor must still ship so a
		# downstream repricer can't drive the Amazon price below it. our_price stays gated.
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
				"send_min_price_to_amazon": 0,
			},
		):
			feed_result = build_pricing_feed([pricing_name], "SELLER", "MARKETPLACE")

		feed = frappe.parse_json(feed_result["feed_content"])
		offers = [
			patch_entry["value"][0]
			for message in feed["messages"]
			for patch_entry in message["patches"]
			if patch_entry["path"] == "/attributes/purchasable_offer"
		]
		self.assertEqual(len(offers), 1)
		self.assertIn("minimum_seller_allowed_price", offers[0])
		self.assertNotIn("our_price", offers[0])
		# Same fixture band as above: min floor must still ship exactly at 5.56 even with our_price gated off.
		self.assertAlmostEqual(self.offer_value(offers[0], "minimum_seller_allowed_price"), 5.56, places=2)

	def test_send_min_price_publishes_our_price_at_max(self):
		# With send_min_price_to_amazon ON, production sets our_price = round(final_max_price, 2).
		# A regression that pins our_price to the MIN (5.56) instead of the MAX (6.25) must go red here.
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
				"send_min_price_to_amazon": 1,
			},
		):
			feed_result = build_pricing_feed([pricing_name], "SELLER", "MARKETPLACE")

		feed, offer = self.get_single_offer(feed_result)
		self.assertIn("our_price", offer)
		# our_price pinned to the max of the band (6.25), NOT the min.
		self.assertAlmostEqual(self.offer_value(offer, "our_price"), 6.25, places=2)
		self.assertAlmostEqual(
			self.offer_value(offer, "our_price"),
			self.offer_value(offer, "maximum_seller_allowed_price"),
			places=2,
		)
		self.assertNotAlmostEqual(
			self.offer_value(offer, "our_price"),
			self.offer_value(offer, "minimum_seller_allowed_price"),
			places=2,
		)
		# UK/GBP marketplace indicator lives in the feed header locale (the schedule carries no
		# explicit currency_code field in this feed shape).
		self.assertEqual(feed["header"]["issueLocale"], "en_GB")

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
				purchase_cost_sources,
				"get_bundle_components",
				return_value={"BUNDLE-ITEM": [{"item_code": "C1", "qty": 2}, {"item_code": "C2", "qty": 1}]},
			),
			patch.object(
				purchase_cost_sources,
				"get_free_qty_map",
				return_value={"C1": {"free_qty": 3}, "C2": {"free_qty": 100}},
			),
		):
			buildable_map = purchase_cost_sources.get_buildable_bundle_qty_map(["BUNDLE-ITEM"])

		self.assertEqual(buildable_map["BUNDLE-ITEM"], 1)


STOCK_ITEM_CODE = "TEST-FEED-STOCK-ITEM"
STOCK_SEED_QTY = 1_000_000


def create_stock_item():
	if frappe.db.exists("Item", STOCK_ITEM_CODE):
		return STOCK_ITEM_CODE

	item = frappe.new_doc("Item")
	item.item_code = STOCK_ITEM_CODE
	item.item_name = STOCK_ITEM_CODE
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 1
	item.insert()
	return item.name


def seed_bin_stock(item_code, qty=STOCK_SEED_QTY):
	# A read-only stock stub: get_free_qty_map sums Bin.actual_qty - reserved_qty over
	# non-rejected warehouses, so a single Bin row is enough to drive mfn_stock > 0.
	warehouse = frappe.db.get_value(
		"Warehouse",
		{"is_group": 0, "is_rejected_warehouse": 0, "disabled": 0},
		"name",
	)
	if not warehouse:
		raise RuntimeError("No usable non-rejected warehouse on this site to seed Bin stock")

	existing = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "name")
	if existing:
		bin_doc = frappe.get_doc("Bin", existing)
		bin_doc.actual_qty = qty
		bin_doc.reserved_qty = 0
		bin_doc.save(ignore_permissions=True)
		return warehouse

	bin_doc = frappe.new_doc("Bin")
	bin_doc.item_code = item_code
	bin_doc.warehouse = warehouse
	bin_doc.actual_qty = qty
	bin_doc.reserved_qty = 0
	bin_doc.insert(ignore_permissions=True)
	return warehouse


def create_stock_proxy_sku(item_code, selling_price=None):
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.is_active = 1
	proxy_sku.item = item_code
	proxy_sku.uom = "Nos"
	if selling_price is not None:
		proxy_sku.current_selling_price = selling_price
	proxy_sku.insert()
	return proxy_sku.name


def create_stock_strategy(proxy_sku, purchase_cost_value=5.0):
	strategy = frappe.new_doc("Amazon Product Strategy")
	strategy.proxy_sku = proxy_sku
	strategy.strategy_name = "default"
	strategy.is_active = 1
	strategy.min_margin = 10
	strategy.max_margin = 20
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


def create_stock_pricing(proxy_sku, strategy, current_min=None, current_max=None):
	pricing = frappe.new_doc("Amazon Product Pricing")
	pricing.proxy_sku = proxy_sku
	pricing.is_active = 1
	pricing.default_strategy = strategy.name
	pricing.insert()
	if current_min is not None or current_max is not None:
		frappe.db.set_value(
			"Amazon Product Pricing",
			pricing.name,
			{
				"current_min_price": current_min or 0,
				"current_max_price": current_max or 0,
			},
			update_modified=False,
		)
	return pricing.name


def apply_navgold_settings(**fields):
	# change_settings does a raw setattr; use Document.set so child-table fields
	# (zero_stock_items / published_pricing_items) are coerced into real child docs.
	settings = frappe.get_doc("Navgold Settings")
	for key, value in fields.items():
		settings.set(key, value)
	settings.save(ignore_permissions=True)


def message_skus(feed_result):
	feed = frappe.parse_json(feed_result["feed_content"])
	return [message["sku"] for message in feed["messages"]]


def quantity_for_sku(feed_result, sku):
	feed = frappe.parse_json(feed_result["feed_content"])
	for message in feed["messages"]:
		if message["sku"] != sku:
			continue
		for patch_entry in message["patches"]:
			if patch_entry["path"] == "/attributes/fulfillment_availability":
				return patch_entry["value"][0].get("quantity")
	return None


def offer_paths_for_sku(feed_result, sku):
	feed = frappe.parse_json(feed_result["feed_content"])
	for message in feed["messages"]:
		if message["sku"] != sku:
			continue
		return [patch_entry["path"] for patch_entry in message["patches"]]
	return None


class TestFeedBuilderStockPublishingMode(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_purchase_cost_type()
		create_stock_item()
		seed_bin_stock(STOCK_ITEM_CODE)

	def tearDown(self):
		# Reset the shared singleton so a mode leak can't turn a neighbour green/red by accident.
		apply_navgold_settings(
			stock_publishing_mode="Publish Actual Stock",
			pricing_publishing_mode="Publish All Prices",
			zero_stock_items=[],
			published_pricing_items=[],
			stock_reserve_pct=0,
			max_publish_qty=0,
		)

	def build_stock_only_feed(self, pricing_names):
		return build_pricing_feed(pricing_names, "SELLER", "MARKETPLACE")

	def test_actual_stock_mode_emits_real_positive_quantity(self):
		# Control: proves the seeded item genuinely has stock, so a forced-zero elsewhere is
		# the mode's doing, not an empty bin.
		proxy_sku = create_stock_proxy_sku(STOCK_ITEM_CODE)
		strategy = create_stock_strategy(proxy_sku)
		pricing_name = create_stock_pricing(proxy_sku, strategy)

		apply_navgold_settings(
			amazon_publishing_mode="Stock Only",
			pricing_publishing_mode="Publish All Prices",
			stock_publishing_mode="Publish Actual Stock",
			stock_reserve_pct=0,
			max_publish_qty=0,
		)
		feed_result = self.build_stock_only_feed([pricing_name])

		self.assertIn(proxy_sku, message_skus(feed_result))
		self.assertGreater(quantity_for_sku(feed_result, proxy_sku), 0)

	def test_publish_zero_stock_for_all_forces_quantity_zero(self):
		# [oversell guard] An in-stock SKU must ship quantity 0 under the all-items zero mode.
		proxy_sku = create_stock_proxy_sku(STOCK_ITEM_CODE)
		strategy = create_stock_strategy(proxy_sku)
		pricing_name = create_stock_pricing(proxy_sku, strategy)

		apply_navgold_settings(
			amazon_publishing_mode="Stock Only",
			pricing_publishing_mode="Publish All Prices",
			stock_publishing_mode="Publish Zero Stock for All Items",
			stock_reserve_pct=0,
			max_publish_qty=0,
		)
		feed_result = self.build_stock_only_feed([pricing_name])

		self.assertIn(proxy_sku, message_skus(feed_result))
		self.assertEqual(quantity_for_sku(feed_result, proxy_sku), 0)

	def test_publish_zero_stock_for_selected_only_zeros_selected_sku(self):
		# [oversell guard] Selected SKU -> quantity 0; a non-selected in-stock SKU keeps real stock.
		selected_proxy = create_stock_proxy_sku(STOCK_ITEM_CODE)
		selected_strategy = create_stock_strategy(selected_proxy)
		selected_pricing = create_stock_pricing(selected_proxy, selected_strategy)

		unselected_proxy = create_stock_proxy_sku(STOCK_ITEM_CODE)
		unselected_strategy = create_stock_strategy(unselected_proxy)
		unselected_pricing = create_stock_pricing(unselected_proxy, unselected_strategy)

		apply_navgold_settings(
			amazon_publishing_mode="Stock Only",
			pricing_publishing_mode="Publish All Prices",
			stock_publishing_mode="Publish Zero Stock for Selected Items",
			zero_stock_items=[{"proxy_sku": selected_proxy}],
			stock_reserve_pct=0,
			max_publish_qty=0,
		)
		feed_result = self.build_stock_only_feed([selected_pricing, unselected_pricing])

		self.assertEqual(quantity_for_sku(feed_result, selected_proxy), 0)
		self.assertGreater(quantity_for_sku(feed_result, unselected_proxy), 0)


class TestFeedBuilderPricingPublishingMode(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_purchase_cost_type()
		create_stock_item()
		seed_bin_stock(STOCK_ITEM_CODE)

	def tearDown(self):
		apply_navgold_settings(
			pricing_publishing_mode="Publish All Prices",
			published_pricing_items=[],
		)

	def test_publish_selected_prices_only_excludes_unselected_sku(self):
		# A SKU absent from the selected-prices list must not appear in the feed at all
		# (its purchasable_offer is never emitted); a selected SKU keeps its price patch.
		selected_proxy = create_stock_proxy_sku(STOCK_ITEM_CODE, selling_price=8.5)
		selected_strategy = create_stock_strategy(selected_proxy)
		selected_pricing = create_stock_pricing(
			selected_proxy, selected_strategy, current_min=8, current_max=9
		)

		unselected_proxy = create_stock_proxy_sku(STOCK_ITEM_CODE, selling_price=8.5)
		unselected_strategy = create_stock_strategy(unselected_proxy)
		unselected_pricing = create_stock_pricing(
			unselected_proxy, unselected_strategy, current_min=8, current_max=9
		)

		apply_navgold_settings(
			amazon_publishing_mode="Stock and Prices",
			pricing_publishing_mode="Publish Selected Prices Only",
			published_pricing_items=[{"proxy_sku": selected_proxy}],
			price_change_threshold_percent=50,
			price_change_block_direction="Both",
			stock_reserve_pct=0,
			max_publish_qty=0,
		)
		feed_result = build_pricing_feed([selected_pricing, unselected_pricing], "SELLER", "MARKETPLACE")

		skus = message_skus(feed_result)
		self.assertIn(selected_proxy, skus)
		self.assertNotIn(unselected_proxy, skus)
		self.assertIn("/attributes/purchasable_offer", offer_paths_for_sku(feed_result, selected_proxy))


class TestFeedBuilderPriceDeviationBlock(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_purchase_cost_type()
		create_stock_item()
		seed_bin_stock(STOCK_ITEM_CODE)

	def tearDown(self):
		apply_navgold_settings(
			pricing_publishing_mode="Publish All Prices",
			published_pricing_items=[],
		)

	def test_price_deviation_excludes_sku_and_records_block(self):
		# [money guard] A live selling price far outside the min/max band must block the SKU
		# from the price feed (blocked_price_deviation), while an in-band SKU still ships.
		blocked_proxy = create_stock_proxy_sku(STOCK_ITEM_CODE, selling_price=100)
		blocked_strategy = create_stock_strategy(blocked_proxy)
		blocked_pricing = create_stock_pricing(blocked_proxy, blocked_strategy, current_min=5, current_max=6)

		allowed_proxy = create_stock_proxy_sku(STOCK_ITEM_CODE, selling_price=8.5)
		allowed_strategy = create_stock_strategy(allowed_proxy)
		allowed_pricing = create_stock_pricing(allowed_proxy, allowed_strategy, current_min=8, current_max=9)

		apply_navgold_settings(
			amazon_publishing_mode="Stock and Prices",
			pricing_publishing_mode="Publish All Prices",
			price_change_threshold_percent=1,
			price_change_block_direction="Both",
			stock_reserve_pct=0,
			max_publish_qty=0,
		)
		feed_result = build_pricing_feed([blocked_pricing, allowed_pricing], "SELLER", "MARKETPLACE")

		blocked_skus = [record["proxy_sku"] for record in feed_result["blocked_price_deviation"]]
		self.assertIn(blocked_proxy, blocked_skus)

		skus = message_skus(feed_result)
		self.assertNotIn(blocked_proxy, skus)
		self.assertIn(allowed_proxy, skus)


class TestFeedBuilderInventoryChannels(IntegrationTestCase):
	# Direct get_sku_inventory_data + build_inventory_patches seam (the same seam the
	# TestBundleZeroStock class uses) to cover the FBA and backorder shipping-group forks
	# without needing live AFN/restock seeding through the batch layer.
	def base_proxy_sku_info(self, shipping_group_choice=None):
		return {
			"SKU-CH": {
				"item": "CH-ITEM",
				"uom": "Nos",
				"pool_size": 1,
				"pack_size": 1.0,
				"merchant_shipping_group": shipping_group_choice,
			}
		}

	def test_fba_active_with_stock_emits_zero_qty_amazon_channel(self):
		# [oversell guard] FBA-fulfilled SKUs must never publish a merchant quantity; Amazon owns it.
		pricing = frappe._dict(
			proxy_sku="SKU-CH",
			active_strategy="STRAT-fba",
			fba_strategy="STRAT-fba",
			sfp_strategy=None,
			backorder_strategy=None,
		)
		stock_info_map = {
			"SKU-CH": {"mfn_stock": 500, "fba_stock": 42, "has_restock_date": False, "restock_date": None}
		}
		inventory_data = get_sku_inventory_data(
			pricing,
			None,
			self.base_proxy_sku_info(),
			stock_info_map,
			supplier_data_map={},
			lead_time_map={},
			bundle_buildable_map={},
		)

		self.assertTrue(inventory_data["is_fba"])
		self.assertEqual(inventory_data["quantity"], 0)
		self.assertEqual(inventory_data["fulfillment_channel"], "AMAZON_EU")

		patches = build_inventory_patches(inventory_data, "MARKETPLACE")
		fulfillment_patch = next(
			patch_entry
			for patch_entry in patches
			if patch_entry["path"] == "/attributes/fulfillment_availability"
		)
		self.assertEqual(fulfillment_patch["value"][0]["fulfillment_channel_code"], "AMAZON_EU")
		self.assertNotIn("quantity", fulfillment_patch["value"][0])

	def test_sfp_active_emits_sfp_shipping_group(self):
		pricing = frappe._dict(
			proxy_sku="SKU-CH",
			active_strategy="STRAT-sfp",
			fba_strategy=None,
			sfp_strategy="STRAT-sfp",
			backorder_strategy=None,
		)
		stock_info_map = {
			"SKU-CH": {"mfn_stock": 10, "fba_stock": 0, "has_restock_date": False, "restock_date": None}
		}
		inventory_data = get_sku_inventory_data(
			pricing,
			None,
			self.base_proxy_sku_info(),
			stock_info_map,
			supplier_data_map={},
			lead_time_map={},
			bundle_buildable_map={},
		)

		self.assertEqual(inventory_data["shipping_group"], SHIPPING_GROUPS["sfp"])

		patches = build_inventory_patches(inventory_data, "MARKETPLACE")
		shipping_patch = next(
			patch_entry
			for patch_entry in patches
			if patch_entry["path"] == "/attributes/merchant_shipping_group"
		)
		self.assertEqual(shipping_patch["value"][0]["value"], SHIPPING_GROUPS["sfp"])

	def test_non_sfp_default_emits_main_revised_shipping_group(self):
		pricing = frappe._dict(
			proxy_sku="SKU-CH",
			active_strategy="STRAT-default",
			fba_strategy=None,
			sfp_strategy=None,
			backorder_strategy=None,
		)
		stock_info_map = {
			"SKU-CH": {"mfn_stock": 10, "fba_stock": 0, "has_restock_date": False, "restock_date": None}
		}
		inventory_data = get_sku_inventory_data(
			pricing,
			None,
			self.base_proxy_sku_info(),
			stock_info_map,
			supplier_data_map={},
			lead_time_map={},
			bundle_buildable_map={},
		)

		self.assertEqual(inventory_data["shipping_group"], SHIPPING_GROUPS["main_revised"])
