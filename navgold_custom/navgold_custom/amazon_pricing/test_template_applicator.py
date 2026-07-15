# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.cost_tree_test_fixtures import (
	create_proxy_sku,
	ensure_cost_group,
	ensure_cost_leaf,
)
from navgold_custom.navgold_custom.amazon_pricing.template_applicator import (
	apply_template_to_pricing,
	apply_template_to_proxy_sku,
	run_bulk_apply_to_proxy_skus,
)

TEMPLATE_NAME = "Test Bulk Template"
STRATEGY_TYPES = ["FBA", "SFP", "Backorder", "Default"]

TREE_GROUP = "Test Template Absolute"
TREE_LEAF = f"{TREE_GROUP}-Overhead"
COST_SET_TEMPLATE = "Test Template Cost Set"
COST_SET_STRATEGY_TEMPLATE = "Test Cost Set Strategy Template"
LEAF_DEFAULT_VALUE = 5.0


def ensure_cost_tree_leaf():
	ensure_cost_group(TREE_GROUP)
	# Self-contained leaf (no source): Auto resolves to default_value, keeps the fixture offline-safe.
	ensure_cost_leaf(TREE_GROUP, "Overhead", default_value=LEAF_DEFAULT_VALUE)


def create_cost_set_strategy_template():
	if frappe.db.exists("Amazon Strategy Template", COST_SET_STRATEGY_TEMPLATE):
		return COST_SET_STRATEGY_TEMPLATE

	ensure_cost_tree_leaf()

	if not frappe.db.exists("Amazon Cost Set Template", COST_SET_TEMPLATE):
		cost_set = frappe.new_doc("Amazon Cost Set Template")
		cost_set.template_name = COST_SET_TEMPLATE
		cost_set.append(
			"costs",
			{
				"cost_type": TREE_GROUP,
				"cost_sub_type": TREE_LEAF,
				"default_value": LEAF_DEFAULT_VALUE,
				"rank": 1,
			},
		)
		cost_set.insert()

	template = frappe.new_doc("Amazon Strategy Template")
	template.template_name = COST_SET_STRATEGY_TEMPLATE
	template.append(
		"strategies",
		{
			"strategy_type": "FBA",
			"strategy_name": "FBA",
			"min_margin": 10,
			"max_margin": 20,
			"cost_set_template": COST_SET_TEMPLATE,
		},
	)
	template.insert()
	return template.name


def create_strategy_template():
	if frappe.db.exists("Amazon Strategy Template", TEMPLATE_NAME):
		return TEMPLATE_NAME

	template = frappe.new_doc("Amazon Strategy Template")
	template.template_name = TEMPLATE_NAME
	for strategy_type in STRATEGY_TYPES:
		# No cost_set_template / repricer_rule: both optional, keeps fixtures offline-safe.
		template.append(
			"strategies",
			{
				"strategy_type": strategy_type,
				"strategy_name": strategy_type,
				"min_margin": 10,
				"max_margin": 20,
			},
		)
	template.insert()
	return template.name


class TestTemplateApplicator(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.template_name = create_strategy_template()

	def test_apply_to_proxy_sku_creates_pricing_and_strategies(self):
		proxy_sku = create_proxy_sku()

		result = apply_template_to_proxy_sku(self.template_name, proxy_sku)

		self.assertEqual(result["status"], "applied")
		self.assertEqual(result["strategies_created"], len(STRATEGY_TYPES))

		pricing_name = frappe.db.get_value("Amazon Product Pricing", {"proxy_sku": proxy_sku}, "name")
		self.assertTrue(pricing_name)

		pricing = frappe.get_doc("Amazon Product Pricing", pricing_name)
		self.assertTrue(pricing.fba_strategy)
		self.assertTrue(pricing.sfp_strategy)
		self.assertTrue(pricing.backorder_strategy)
		self.assertTrue(pricing.default_strategy)

		strategy_count = frappe.db.count("Amazon Product Strategy", {"proxy_sku": proxy_sku})
		self.assertEqual(strategy_count, len(STRATEGY_TYPES))

	def test_apply_to_proxy_sku_skips_when_strategies_exist(self):
		proxy_sku = create_proxy_sku()
		apply_template_to_proxy_sku(self.template_name, proxy_sku)

		result = apply_template_to_proxy_sku(self.template_name, proxy_sku)

		self.assertEqual(result["status"], "skipped")
		self.assertEqual(result["strategies_created"], 0)

		# Re-run must not create duplicate strategies.
		strategy_count = frappe.db.count("Amazon Product Strategy", {"proxy_sku": proxy_sku})
		self.assertEqual(strategy_count, len(STRATEGY_TYPES))

	def test_apply_to_proxy_sku_with_existing_pricing_no_strategies(self):
		proxy_sku = create_proxy_sku()
		pricing = frappe.new_doc("Amazon Product Pricing")
		pricing.proxy_sku = proxy_sku
		pricing.is_active = 1
		pricing.insert()

		result = apply_template_to_proxy_sku(self.template_name, proxy_sku)

		self.assertEqual(result["status"], "applied")
		self.assertEqual(result["strategies_created"], len(STRATEGY_TYPES))

		pricing.reload()
		self.assertTrue(pricing.fba_strategy)
		self.assertTrue(pricing.default_strategy)

	def test_apply_template_to_pricing_envelope_regression(self):
		proxy_sku = create_proxy_sku()
		pricing = frappe.new_doc("Amazon Product Pricing")
		pricing.proxy_sku = proxy_sku
		pricing.is_active = 1
		pricing.insert()

		result = apply_template_to_pricing(self.template_name, pricing.name)

		self.assertIs(result["success"], True)
		self.assertEqual(result["strategies_created"], len(STRATEGY_TYPES))

	def test_apply_to_proxy_sku_inherits_inactive_from_proxy_sku(self):
		# New pricing must inherit is_active from the Proxy SKU, not hardcode 1, the action
		# deliberately permits inactive selections.
		proxy_sku = create_proxy_sku(is_active=0)

		apply_template_to_proxy_sku(self.template_name, proxy_sku)

		is_active = frappe.db.get_value("Amazon Product Pricing", {"proxy_sku": proxy_sku}, "is_active")
		self.assertEqual(is_active, 0)

	def test_template_seeds_cost_rows_pointing_at_the_cost_type_tree(self):
		# A cost-set-backed template must land cost rows on the tree: cost_type = group node,
		# cost_sub_type = leaf node, value_mode Auto derives from the leaf default, price derives.
		template_name = create_cost_set_strategy_template()
		proxy_sku = create_proxy_sku()

		apply_template_to_proxy_sku(template_name, proxy_sku)

		strategy_name = frappe.db.get_value("Amazon Product Strategy", {"proxy_sku": proxy_sku}, "name")
		strategy = frappe.get_doc("Amazon Product Strategy", strategy_name)

		self.assertEqual(len(strategy.costs), 1)
		cost = strategy.costs[0]
		self.assertEqual(cost.cost_type, TREE_GROUP)
		self.assertEqual(cost.cost_sub_type, TREE_LEAF)
		self.assertEqual(frappe.db.get_value("Cost Type", cost.cost_sub_type, "is_group"), 0)
		self.assertEqual(cost.value_mode, "Auto")
		self.assertEqual(cost.value, LEAF_DEFAULT_VALUE)
		self.assertGreater(strategy.calculated_min_price, 0)

	def test_run_bulk_rolls_back_failed_item_without_poisoning_batch(self):
		# A bad item (non-existent Proxy SKU) must roll back to its own savepoint and leave the
		# good item applied, one failure cannot cascade-fail the rest of the batch.
		good_proxy_sku = create_proxy_sku()
		bad_proxy_sku = "NON-EXISTENT-PROXY-SKU-ZZZ"

		tally = run_bulk_apply_to_proxy_skus(self.template_name, [good_proxy_sku, bad_proxy_sku])

		self.assertEqual(tally["applied"], 1)
		self.assertEqual(tally["errors"], 1)
		self.assertEqual(tally["skipped"], 0)

		# Good item fully applied despite the sibling failure.
		self.assertTrue(frappe.db.exists("Amazon Product Pricing", {"proxy_sku": good_proxy_sku}))
		# Failed item left nothing behind (savepoint rollback).
		self.assertFalse(frappe.db.exists("Amazon Product Strategy", {"proxy_sku": bad_proxy_sku}))
