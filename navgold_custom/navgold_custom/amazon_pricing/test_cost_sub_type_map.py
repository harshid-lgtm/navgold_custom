# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.cost_tree_test_fixtures import (
	build_strategy,
	create_proxy_sku,
	ensure_cost_group,
	ensure_cost_leaf,
	ensure_item,
)
from navgold_custom.navgold_custom.amazon_pricing.price_calculator import get_cost_sub_type_map
from navgold_custom.navgold_custom.doctype.amazon_product_strategy import amazon_product_strategy

GROUP = "Test Map Absolute"
FED_LEAF = f"{GROUP}-Top Supplier"
DEFAULTED_LEAF = f"{GROUP}-Overhead"
ITEM_CODE = "TEST-MAP-ITEM"


def build_map_strategy(rows):
	return build_strategy(create_proxy_sku(item=ITEM_CODE, uom="Nos"), rows, strategy_name="Map")


class TestCostSubTypeMap(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cost_group(GROUP)
		ensure_cost_leaf(GROUP, "Top Supplier", source="Supplier-Sync")
		ensure_cost_leaf(GROUP, "Overhead", default_value=3.5)
		ensure_item(ITEM_CODE)

	def test_map_mirrors_cost_type_leaves(self):
		# The map is exactly the value config of every non-group Cost Type node.
		expected = {
			row.name: {
				"source": row.source,
				"default_value": row.default_value,
				"allowed_zero_valued": row.allowed_zero_valued,
			}
			for row in frappe.get_all(
				"Cost Type",
				filters={"is_group": 0},
				fields=["name", "source", "default_value", "allowed_zero_valued"],
			)
		}
		self.assertEqual(get_cost_sub_type_map(), expected)

	def test_map_excludes_group_nodes(self):
		self.assertNotIn(GROUP, get_cost_sub_type_map())

	def test_fed_leaf_resolves_from_feed_through_flipped_reader(self):
		with patch.dict(
			amazon_product_strategy.SOURCE_RESOLVERS,
			{"Supplier-Sync": lambda context: 7.25},
		):
			strategy = build_map_strategy(
				[{"cost_type": GROUP, "cost_sub_type": FED_LEAF, "value": 0, "rank": 1}]
			)
		self.assertEqual(strategy.costs[0].value_mode, "Auto")
		self.assertEqual(strategy.costs[0].value, 7.25)

	def test_defaulted_leaf_uses_default_value_through_flipped_reader(self):
		strategy = build_map_strategy(
			[{"cost_type": GROUP, "cost_sub_type": DEFAULTED_LEAF, "value": 0, "rank": 1}]
		)
		self.assertEqual(strategy.costs[0].value_mode, "Auto")
		self.assertEqual(strategy.costs[0].value, 3.5)
