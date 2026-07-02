# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.doctype.amazon_product_strategy import amazon_product_strategy

ABSOLUTE_COST_TYPE = "Test Remodel Absolute"
FED_SUB_TYPE = f"{ABSOLUTE_COST_TYPE}-Top Supplier"
DEFAULTED_SUB_TYPE = f"{ABSOLUTE_COST_TYPE}-Overhead"
ITEM_CODE = "TEST-REMODEL-ITEM"


def ensure_cost_type():
	if not frappe.db.exists("Cost Type", ABSOLUTE_COST_TYPE):
		cost_type = frappe.new_doc("Cost Type")
		cost_type.cost_type_name = ABSOLUTE_COST_TYPE
		cost_type.is_group = 1
		cost_type.calculation_type = "Absolute"
		cost_type.insert()


def ensure_sub_type(name, sub_type_name, source="", default_value=0):
	if frappe.db.exists("Cost Type", name):
		return name
	sub_type = frappe.new_doc("Cost Type")
	sub_type.is_group = 0
	sub_type.parent_cost_type = ABSOLUTE_COST_TYPE
	sub_type.cost_type_name = sub_type_name
	sub_type.source = source
	sub_type.default_value = default_value
	sub_type.insert()
	return sub_type.name


def ensure_item():
	if frappe.db.exists("Item", ITEM_CODE):
		return ITEM_CODE
	item = frappe.new_doc("Item")
	item.item_code = ITEM_CODE
	item.item_name = ITEM_CODE
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 0
	item.insert()
	return item.name


def build_strategy(rows):
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.item = ITEM_CODE
	proxy_sku.uom = "Nos"
	proxy_sku.is_active = 1
	proxy_sku.insert()

	strategy = frappe.new_doc("Amazon Product Strategy")
	strategy.proxy_sku = proxy_sku.name
	strategy.strategy_name = "Remodel"
	strategy.is_active = 1
	strategy.min_margin = 5
	strategy.max_margin = 10
	for row in rows:
		strategy.append("costs", row)
	strategy.insert()
	return strategy


class TestCostSourceRemodel(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cost_type()
		ensure_item()
		ensure_sub_type(FED_SUB_TYPE, "Top Supplier", source="Supplier-Sync")
		ensure_sub_type(DEFAULTED_SUB_TYPE, "Overhead", source="", default_value=3.5)

	def test_validate_rejects_source_with_default_value(self):
		sub_type = frappe.new_doc("Cost Sub Type")
		sub_type.cost_type = ABSOLUTE_COST_TYPE
		sub_type.sub_type_name = "Bad Fed"
		sub_type.source = "Supplier-Sync"
		sub_type.default_value = 12
		with self.assertRaises(frappe.ValidationError):
			sub_type.insert()

	def test_validate_allows_default_value_when_self_contained(self):
		sub_type = frappe.new_doc("Cost Sub Type")
		sub_type.cost_type = ABSOLUTE_COST_TYPE
		sub_type.sub_type_name = "Good Overhead"
		sub_type.source = ""
		sub_type.default_value = 9
		sub_type.insert()
		self.assertEqual(sub_type.default_value, 9)
		frappe.delete_doc("Cost Sub Type", sub_type.name, force=True)

	def test_defaulted_sub_type_uses_default_value(self):
		strategy = build_strategy(
			[{"cost_type": ABSOLUTE_COST_TYPE, "cost_sub_type": DEFAULTED_SUB_TYPE, "value": 0, "rank": 1}]
		)
		cost = strategy.costs[0]
		self.assertEqual(cost.value_mode, "Auto")
		self.assertEqual(cost.value, 3.5)

	def test_fed_sub_type_resolves_from_feed(self):
		# Patch the Supplier-Sync resolver so the test is deterministic and DB-independent.
		with patch.dict(
			amazon_product_strategy.ITEM_SYNCED_SOURCE_RESOLVERS,
			{"Supplier-Sync": lambda context: 7.25},
		):
			strategy = build_strategy(
				[{"cost_type": ABSOLUTE_COST_TYPE, "cost_sub_type": FED_SUB_TYPE, "value": 0, "rank": 1}]
			)
		cost = strategy.costs[0]
		self.assertEqual(cost.value_mode, "Auto")
		self.assertEqual(cost.value, 7.25)

	def test_source_options_match_registered_resolvers(self):
		# Guards against drift: a new Cost Sub Type.source option with no resolver (and not externally
		# owned) would silently log-and-skip at runtime. Catch it here instead.
		options = frappe.get_meta("Cost Sub Type").get_field("source").options or ""
		source_options = {option for option in options.split("\n") if option}
		resolvable = source_options - amazon_product_strategy.EXTERNALLY_OWNED_SOURCES
		self.assertEqual(resolvable, set(amazon_product_strategy.ITEM_SYNCED_SOURCE_RESOLVERS))

	def test_external_value_change_pins_manual(self):
		# An Auto row's value changed via a non-form write path (REST/db.set_value/import/script) must be
		# pinned to Manual server-side, and the feed must not clobber it on the next save.
		with patch.dict(
			amazon_product_strategy.ITEM_SYNCED_SOURCE_RESOLVERS,
			{"Supplier-Sync": lambda context: 7.25},
		):
			strategy = build_strategy(
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
			amazon_product_strategy.ITEM_SYNCED_SOURCE_RESOLVERS,
			{"Supplier-Sync": lambda context: 7.25},
		):
			strategy.save()

		self.assertEqual(strategy.costs[0].value_mode, "Manual")
		self.assertEqual(strategy.costs[0].value, 4.00)

	def test_feed_refresh_does_not_spuriously_pin(self):
		# Saving twice with no external value edit: the feed recompute must not be mistaken for a human
		# override, so Auto rows stay Auto.
		with patch.dict(
			amazon_product_strategy.ITEM_SYNCED_SOURCE_RESOLVERS,
			{"Supplier-Sync": lambda context: 7.25},
		):
			strategy = build_strategy(
				[{"cost_type": ABSOLUTE_COST_TYPE, "cost_sub_type": FED_SUB_TYPE, "value": 0, "rank": 1}]
			)
			self.assertEqual(strategy.costs[0].value_mode, "Auto")
			strategy.save()

		self.assertEqual(strategy.costs[0].value_mode, "Auto")
		self.assertEqual(strategy.costs[0].value, 7.25)

	def test_externally_owned_value_change_not_pinned(self):
		# An Amazon-Sync (externally owned) Auto row whose value changes — as the FBA fee fetcher would
		# write it — must NOT be flipped to Manual; it stays owned by that process.
		owned_sub_type = ensure_sub_type(
			f"{ABSOLUTE_COST_TYPE}-Amazon Fee", "Amazon Fee", source="Amazon-Sync"
		)
		strategy = build_strategy(
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
			amazon_product_strategy.ITEM_SYNCED_SOURCE_RESOLVERS,
			{"Supplier-Sync": lambda context: 7.25},
		):
			strategy = build_strategy(
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
