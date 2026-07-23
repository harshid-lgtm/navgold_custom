# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

# End-to-end flow over the merged Cost Type NestedSet tree: a strategy whose cost rows point at
# group nodes (calc rule) + leaf nodes (source/default) is saved, recomputed, and turned into the
# outbound Amazon feed. We drive the real chain (populate_costs -> calculate_prices -> pricing
# select_active_strategy -> build_pricing_feed) and assert the PERSISTED rows + the wire payload.
# The feed builder does no network I/O; the submitter ships feed_result["feed_content"] verbatim
# (amazon_feed_submitter.py:47-98), so asserting on the built payload is faithful to the wire.

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.tests.utils import change_settings

from navgold_custom.navgold_custom.amazon_pricing import cost_tree_test_fixtures, price_calculator
from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import build_pricing_feed

# Pin the Navgold Settings knobs so the band is decided by the formula, not by the restored site.
FIXED_SETTINGS = {"commission_threshold": 10, "high_price_adjustment": 0.07}

PURCHASE_GROUP = "Flow Tree Purchase"
VAT_GROUP = "Flow Tree VAT"
FED_LEAF = f"{PURCHASE_GROUP}-FBATransfer"
DEFAULTED_VAT_LEAF = f"{VAT_GROUP}-StdRate"
PO_SYNC_LEAF = f"{PURCHASE_GROUP}-POSync"
ITEM_CODE = "TEST-COST-TREE-FLOW-ITEM"


def ensure_cost_tree():
	cost_tree_test_fixtures.ensure_cost_group(PURCHASE_GROUP, "Absolute", is_purchase_cost=1)
	cost_tree_test_fixtures.ensure_cost_group(VAT_GROUP, "VAT")
	cost_tree_test_fixtures.ensure_cost_leaf(PURCHASE_GROUP, "FBATransfer", source="FBA-Transfer")
	cost_tree_test_fixtures.ensure_cost_leaf(PURCHASE_GROUP, "POSync", source="PO-Sync")
	cost_tree_test_fixtures.ensure_cost_leaf(VAT_GROUP, "StdRate", default_value=20)


def ensure_item():
	return cost_tree_test_fixtures.ensure_item(ITEM_CODE, is_stock_item=1)


def create_proxy_sku(last_transfer_rate=4.0, selling_price=None):
	return cost_tree_test_fixtures.create_proxy_sku(
		item=ITEM_CODE, uom="Nos", last_transfer_rate=last_transfer_rate, selling_price=selling_price
	)


def build_tree_strategy(proxy_sku):
	# Three rank-1 (active) rows across the tree: a fed leaf, a defaulted leaf under a VAT group,
	# and a pinned Manual row. All three enter the price formula.
	return cost_tree_test_fixtures.build_strategy(
		proxy_sku,
		[
			{"cost_type": PURCHASE_GROUP, "cost_sub_type": FED_LEAF, "value_mode": "Auto", "rank": 1},
			{"cost_type": VAT_GROUP, "cost_sub_type": DEFAULTED_VAT_LEAF, "value_mode": "Auto", "rank": 1},
			{"cost_type": PURCHASE_GROUP, "value_mode": "Manual", "value": 1.0, "rank": 1},
		],
		min_margin=10,
		max_margin=20,
		insert=False,
	)


def insert_with_fixed_settings(strategy):
	return cost_tree_test_fixtures.insert_with_fixed_settings(strategy, FIXED_SETTINGS)


def persisted_costs(strategy_name):
	return frappe.get_all(
		"Amazon Product Cost",
		filters={"parent": strategy_name},
		fields=["cost_type", "cost_sub_type", "value", "value_mode", "is_active"],
		order_by="idx asc",
	)


def create_pricing(proxy_sku, strategy):
	pricing = frappe.new_doc("Amazon Product Pricing")
	pricing.proxy_sku = proxy_sku
	pricing.is_active = 1
	pricing.default_strategy = strategy.name
	pricing.insert()
	return pricing.name


class TestCostTreeFlow(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cost_tree()
		ensure_item()
		# Fresh reference data: drop the redis-cached tree maps so our new nodes resolve.
		price_calculator.get_cost_type_map.clear_cache()
		price_calculator.get_cost_sub_type_map.clear_cache()

	def test_fed_defaulted_and_manual_costs_resolve_on_insert(self):
		# fed leaf -> last_transfer_rate (4), defaulted leaf -> default_value (20), Manual -> pinned (1).
		proxy_sku = create_proxy_sku(last_transfer_rate=4.0)
		strategy = insert_with_fixed_settings(build_tree_strategy(proxy_sku))

		fed, vat, manual = persisted_costs(strategy.name)

		self.assertEqual((fed.cost_type, fed.cost_sub_type), (PURCHASE_GROUP, FED_LEAF))
		self.assertEqual((fed.value, fed.value_mode), (4.0, "Auto"))
		self.assertEqual((vat.cost_type, vat.cost_sub_type), (VAT_GROUP, DEFAULTED_VAT_LEAF))
		self.assertEqual((vat.value, vat.value_mode), (20.0, "Auto"))
		self.assertEqual((manual.value, manual.value_mode), (1.0, "Manual"))

		# numerator = 4 + 1 = 5; denominator = 1 - (0.20/1.20) = 0.83333.
		# min = 5 / (0.83333 - 0.10) = 6.82 ; max = 5 / (0.83333 - 0.20) = 7.89 (both < 10, no adj).
		self.assertAlmostEqual(strategy.calculated_min_price, 6.82, places=2)
		self.assertAlmostEqual(strategy.calculated_max_price, 7.89, places=2)

	def test_group_calc_rule_is_read_from_parent_not_leaf(self):
		proxy_sku = create_proxy_sku(last_transfer_rate=4.0)
		strategy = insert_with_fixed_settings(build_tree_strategy(proxy_sku))

		# The VAT rule lives on the GROUP; the leaf carries none.
		self.assertEqual(price_calculator.get_cost_type_map()[VAT_GROUP].calculation_type, "VAT")
		self.assertTrue(frappe.db.get_value("Cost Type", VAT_GROUP, "is_group"))
		self.assertEqual(frappe.db.get_value("Cost Type", DEFAULTED_VAT_LEAF, "is_group"), 0)

		# Proof the VAT actually entered the formula via the parent: without the VAT fraction the band
		# top-min would be 5 / (1 - 0.10) = 5.56. The persisted 6.82 shows VAT shrank the denominator.
		self.assertNotAlmostEqual(strategy.calculated_min_price, 5.56, places=2)
		self.assertAlmostEqual(strategy.calculated_min_price, 6.82, places=2)

	def test_recompute_rederives_fed_and_defaulted_but_pins_manual(self):
		proxy_sku = create_proxy_sku(last_transfer_rate=4.0)
		strategy = insert_with_fixed_settings(build_tree_strategy(proxy_sku))

		# Feed moves: the fed leaf must follow it on the next save; the Manual row must stay pinned.
		frappe.db.set_value("Proxy SKU", proxy_sku, "last_transfer_rate", 6.0)
		reloaded = frappe.get_doc("Amazon Product Strategy", strategy.name)
		with patch.object(price_calculator, "get_pricing_settings", return_value=FIXED_SETTINGS):
			reloaded.save()

		fed, vat, manual = persisted_costs(strategy.name)
		self.assertEqual((fed.value, fed.value_mode), (6.0, "Auto"))
		self.assertEqual((vat.value, vat.value_mode), (20.0, "Auto"))
		self.assertEqual((manual.value, manual.value_mode), (1.0, "Manual"))

		# numerator = 6 + 1 = 7; min = 7 / (0.83333 - 0.10) = 9.55 (< 10), moved off the original 6.82.
		self.assertAlmostEqual(reloaded.calculated_min_price, 9.55, places=2)
		self.assertNotAlmostEqual(reloaded.calculated_min_price, 6.82, places=2)

	def test_manual_to_auto_switch_is_not_pinned_back(self):
		# Bug: switching a row Manual→Auto zeroes its value in the grid; the pin guard read that drop
		# as an external override and flipped it straight back to Manual, freezing the value at 0.
		proxy_sku = create_proxy_sku(last_transfer_rate=4.0)
		strategy = cost_tree_test_fixtures.build_strategy(
			proxy_sku,
			[
				{
					"cost_type": PURCHASE_GROUP,
					"cost_sub_type": FED_LEAF,
					"value": 1.0,
					"value_mode": "Manual",
					"rank": 1,
				}
			],
			min_margin=10,
			max_margin=20,
			insert=False,
		)
		insert_with_fixed_settings(strategy)

		(row,) = persisted_costs(strategy.name)
		self.assertEqual((row.value, row.value_mode), (1.0, "Manual"))

		# User flips it to Auto; the grid drops the hand-set value to 0 before saving.
		reloaded = frappe.get_doc("Amazon Product Strategy", strategy.name)
		reloaded.costs[0].value_mode = "Auto"
		reloaded.costs[0].value = 0
		with patch.object(price_calculator, "get_pricing_settings", return_value=FIXED_SETTINGS):
			reloaded.save()

		(row,) = persisted_costs(strategy.name)
		self.assertEqual((row.value, row.value_mode), (4.0, "Auto"))

	def test_fed_leaf_resolves_to_zero_when_feed_not_ready(self):
		# A fed leaf whose resolver has no data (PO-Sync, no purchase history) must land at 0, and the
		# row must stay Auto (unsynced) rather than being pinned Manual or holding a stale value.
		proxy_sku = create_proxy_sku(last_transfer_rate=4.0)
		strategy = cost_tree_test_fixtures.build_strategy(
			proxy_sku,
			[
				{
					"cost_type": PURCHASE_GROUP,
					"cost_sub_type": PO_SYNC_LEAF,
					"value": 99,
					"value_mode": "Auto",
					"rank": 1,
				}
			],
			min_margin=10,
			max_margin=20,
			insert=False,
		)
		insert_with_fixed_settings(strategy)

		(row,) = persisted_costs(strategy.name)
		self.assertEqual((row.value, row.value_mode), (0.0, "Auto"))

	def test_outbound_feed_carries_tree_computed_band(self):
		proxy_sku = create_proxy_sku(last_transfer_rate=4.0, selling_price=7.5)
		strategy = insert_with_fixed_settings(build_tree_strategy(proxy_sku))
		pricing_name = create_pricing(proxy_sku, strategy)

		# The pricing must have copied the strategy's tree-computed band as its current band.
		current = frappe.db.get_value(
			"Amazon Product Pricing",
			pricing_name,
			["current_min_price", "current_max_price", "active_strategy"],
			as_dict=True,
		)
		self.assertEqual(current.active_strategy, strategy.name)
		self.assertAlmostEqual(current.current_min_price, 6.82, places=2)
		self.assertAlmostEqual(current.current_max_price, 7.89, places=2)

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

		self.assertEqual(feed_result["blocked_validation"], [])
		self.assertEqual(feed_result["blocked_price_deviation"], [])

		feed = frappe.parse_json(feed_result["feed_content"])
		self.assertEqual(feed["header"]["issueLocale"], "en_GB")
		offers = [
			patch_entry["value"][0]
			for message in feed["messages"]
			for patch_entry in message["patches"]
			if patch_entry["path"] == "/attributes/purchasable_offer"
		]
		self.assertEqual(len(offers), 1)
		offer = offers[0]

		def schedule_value(key):
			return offer[key][0]["schedule"][0]["value_with_tax"]

		# The exact tree-computed band must reach the wire: min floor 6.82, max/our_price 7.89.
		self.assertAlmostEqual(schedule_value("minimum_seller_allowed_price"), 6.82, places=2)
		self.assertAlmostEqual(schedule_value("maximum_seller_allowed_price"), 7.89, places=2)
		self.assertAlmostEqual(schedule_value("our_price"), 7.89, places=2)

	def test_cost_rows_link_group_and_leaf_correctly(self):
		proxy_sku = create_proxy_sku(last_transfer_rate=4.0)
		strategy = insert_with_fixed_settings(build_tree_strategy(proxy_sku))

		for row in persisted_costs(strategy.name):
			self.assertEqual(frappe.db.get_value("Cost Type", row.cost_type, "is_group"), 1)
			if not row.cost_sub_type:
				continue
			leaf = frappe.db.get_value(
				"Cost Type", row.cost_sub_type, ["is_group", "parent_cost_type"], as_dict=True
			)
			self.assertEqual(leaf.is_group, 0)
			self.assertEqual(leaf.parent_cost_type, row.cost_type)
