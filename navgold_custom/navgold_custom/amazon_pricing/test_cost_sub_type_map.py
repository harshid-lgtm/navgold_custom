# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.price_calculator import get_cost_sub_type_map
from navgold_custom.navgold_custom.doctype.amazon_product_strategy import amazon_product_strategy

GROUP = "Test Map Absolute"
FED_LEAF = f"{GROUP}-Top Supplier"
DEFAULTED_LEAF = f"{GROUP}-Overhead"
ITEM_CODE = "TEST-MAP-ITEM"


def ensure_tree():
	if not frappe.db.exists("Cost Type", GROUP):
		group = frappe.new_doc("Cost Type")
		group.cost_type_name = GROUP
		group.is_group = 1
		group.calculation_type = "Absolute"
		group.insert()
	for leaf_name, source, default_value in (
		("Top Supplier", "Supplier-Sync", 0),
		("Overhead", "", 3.5),
	):
		full_name = f"{GROUP}-{leaf_name}"
		if not frappe.db.exists("Cost Type", full_name):
			leaf = frappe.new_doc("Cost Type")
			leaf.is_group = 0
			leaf.parent_cost_type = GROUP
			leaf.cost_type_name = leaf_name
			leaf.source = source
			leaf.default_value = default_value
			leaf.insert()


def ensure_item():
	if not frappe.db.exists("Item", ITEM_CODE):
		item = frappe.new_doc("Item")
		item.item_code = ITEM_CODE
		item.item_name = ITEM_CODE
		item.item_group = "All Item Groups"
		item.stock_uom = "Nos"
		item.is_stock_item = 0
		item.insert()


def build_strategy(rows):
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.item = ITEM_CODE
	proxy_sku.uom = "Nos"
	proxy_sku.is_active = 1
	proxy_sku.insert()

	strategy = frappe.new_doc("Amazon Product Strategy")
	strategy.proxy_sku = proxy_sku.name
	strategy.strategy_name = "Map"
	strategy.is_active = 1
	strategy.min_margin = 5
	strategy.max_margin = 10
	for row in rows:
		strategy.append("costs", row)
	strategy.insert()
	return strategy


class TestCostSubTypeMap(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_tree()
		ensure_item()

	def test_map_mirrors_cost_type_leaves(self):
		# The map is exactly the source/default_value config of every non-group Cost Type node.
		expected = {
			row.name: {"source": row.source, "default_value": row.default_value}
			for row in frappe.get_all(
				"Cost Type", filters={"is_group": 0}, fields=["name", "source", "default_value"]
			)
		}
		self.assertEqual(get_cost_sub_type_map(), expected)

	def test_map_excludes_group_nodes(self):
		self.assertNotIn(GROUP, get_cost_sub_type_map())

	def test_fed_leaf_resolves_from_feed_through_flipped_reader(self):
		with patch.dict(
			amazon_product_strategy.ITEM_SYNCED_SOURCE_RESOLVERS,
			{"Supplier-Sync": lambda context: 7.25},
		):
			strategy = build_strategy(
				[{"cost_type": GROUP, "cost_sub_type": FED_LEAF, "value": 0, "rank": 1}]
			)
		self.assertEqual(strategy.costs[0].value_mode, "Auto")
		self.assertEqual(strategy.costs[0].value, 7.25)

	def test_defaulted_leaf_uses_default_value_through_flipped_reader(self):
		strategy = build_strategy(
			[{"cost_type": GROUP, "cost_sub_type": DEFAULTED_LEAF, "value": 0, "rank": 1}]
		)
		self.assertEqual(strategy.costs[0].value_mode, "Auto")
		self.assertEqual(strategy.costs[0].value, 3.5)
