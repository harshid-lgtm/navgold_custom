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
from navgold_custom.navgold_custom.doctype.amazon_product_strategy import amazon_product_strategy

ABSOLUTE_COST_TYPE = "Test Remodel Absolute"
FED_SUB_TYPE = f"{ABSOLUTE_COST_TYPE}-Top Supplier"
DEFAULTED_SUB_TYPE = f"{ABSOLUTE_COST_TYPE}-Overhead"
ITEM_CODE = "TEST-REMODEL-ITEM"


def build_remodel_strategy(rows):
	return build_strategy(create_proxy_sku(item=ITEM_CODE, uom="Nos"), rows, strategy_name="Remodel")


class TestCostSourceRemodel(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cost_group(ABSOLUTE_COST_TYPE)
		ensure_item(ITEM_CODE)
		ensure_cost_leaf(ABSOLUTE_COST_TYPE, "Top Supplier", source="Supplier-Sync")
		ensure_cost_leaf(ABSOLUTE_COST_TYPE, "Overhead", default_value=3.5)

	def test_defaulted_sub_type_uses_default_value(self):
		strategy = build_remodel_strategy(
			[{"cost_type": ABSOLUTE_COST_TYPE, "cost_sub_type": DEFAULTED_SUB_TYPE, "value": 0, "rank": 1}]
		)
		cost = strategy.costs[0]
		self.assertEqual(cost.value_mode, "Auto")
		self.assertEqual(cost.value, 3.5)

	def test_fed_sub_type_resolves_from_feed(self):
		# Patch the Supplier-Sync resolver so the test is deterministic and DB-independent.
		with patch.dict(
			amazon_product_strategy.SOURCE_RESOLVERS,
			{"Supplier-Sync": lambda context: 7.25},
		):
			strategy = build_remodel_strategy(
				[{"cost_type": ABSOLUTE_COST_TYPE, "cost_sub_type": FED_SUB_TYPE, "value": 0, "rank": 1}]
			)
		cost = strategy.costs[0]
		self.assertEqual(cost.value_mode, "Auto")
		self.assertEqual(cost.value, 7.25)

	def test_source_options_match_registered_resolvers(self):
		# Guards against drift: a new Cost Type.source option missing from the registry would silently
		# log-and-skip at runtime. Every option needs an entry (a resolver, or None = externally owned).
		options = frappe.get_meta("Cost Type").get_field("source").options or ""
		source_options = {option for option in options.split("\n") if option}
		self.assertEqual(source_options, set(amazon_product_strategy.SOURCE_RESOLVERS))

	def test_external_value_change_pins_manual(self):
		# An Auto row's value changed via a non-form write path (REST/db.set_value/import/script) must be
		# pinned to Manual server-side, and the feed must not clobber it on the next save.
		with patch.dict(
			amazon_product_strategy.SOURCE_RESOLVERS,
			{"Supplier-Sync": lambda context: 7.25},
		):
			strategy = build_remodel_strategy(
				[{"cost_type": ABSOLUTE_COST_TYPE, "cost_sub_type": FED_SUB_TYPE, "value": 0, "rank": 1}]
			)
			self.assertEqual(strategy.costs[0].value_mode, "Auto")
			self.assertEqual(strategy.costs[0].value, 7.25)

			# Simulate a non-form write: set the field directly and save (value_mode stays Auto).
			strategy.costs[0].value = 4.00
			strategy.save()

		self.assertEqual(strategy.costs[0].value_mode, "Manual")
		self.assertEqual(strategy.costs[0].value, 4.00)

		# Feed would resolve to 7.25 again; pinned Manual row must survive a subsequent save.
		with patch.dict(
			amazon_product_strategy.SOURCE_RESOLVERS,
			{"Supplier-Sync": lambda context: 7.25},
		):
			strategy.save()

		self.assertEqual(strategy.costs[0].value_mode, "Manual")
		self.assertEqual(strategy.costs[0].value, 4.00)

	def test_feed_refresh_does_not_spuriously_pin(self):
		# Saving twice with no external value edit: the feed recompute must not be mistaken for a human
		# override, so Auto rows stay Auto.
		with patch.dict(
			amazon_product_strategy.SOURCE_RESOLVERS,
			{"Supplier-Sync": lambda context: 7.25},
		):
			strategy = build_remodel_strategy(
				[{"cost_type": ABSOLUTE_COST_TYPE, "cost_sub_type": FED_SUB_TYPE, "value": 0, "rank": 1}]
			)
			self.assertEqual(strategy.costs[0].value_mode, "Auto")
			strategy.save()

		self.assertEqual(strategy.costs[0].value_mode, "Auto")
		self.assertEqual(strategy.costs[0].value, 7.25)

	def test_externally_owned_value_change_not_pinned(self):
		# An Amazon-Sync (externally owned) Auto row whose value changes — as the FBA fee fetcher would
		# write it — must NOT be flipped to Manual; it stays owned by that process.
		owned_sub_type = ensure_cost_leaf(ABSOLUTE_COST_TYPE, "Amazon Fee", source="Amazon-Sync")
		strategy = build_remodel_strategy(
			[{"cost_type": ABSOLUTE_COST_TYPE, "cost_sub_type": owned_sub_type, "value": 2.00, "rank": 1}]
		)
		self.assertEqual(strategy.costs[0].value_mode, "Auto")
		self.assertEqual(strategy.costs[0].value, 2.00)

		# FBA fee fetcher rewrites the value, keeps value_mode=Auto.
		strategy.costs[0].value = 3.50
		strategy.save()

		self.assertEqual(strategy.costs[0].value_mode, "Auto")
		self.assertEqual(strategy.costs[0].value, 3.50)

	def test_manual_row_is_sticky(self):
		# A Manual row on a fed sub-type must keep its hand-pinned value even though the feed would resolve.
		with patch.dict(
			amazon_product_strategy.SOURCE_RESOLVERS,
			{"Supplier-Sync": lambda context: 7.25},
		):
			strategy = build_remodel_strategy(
				[
					{
						"cost_type": ABSOLUTE_COST_TYPE,
						"cost_sub_type": FED_SUB_TYPE,
						"value": 99,
						"value_mode": "Manual",
						"rank": 1,
					}
				]
			)
		cost = strategy.costs[0]
		self.assertEqual(cost.value_mode, "Manual")
		self.assertEqual(cost.value, 99)
