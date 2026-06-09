# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.template_applicator import (
	apply_template_to_pricing,
	apply_template_to_proxy_sku,
	run_bulk_apply_to_proxy_skus,
)

TEMPLATE_NAME = "Test Bulk Template"
STRATEGY_TYPES = ["FBA", "SFP", "Backorder", "Default"]


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


def create_proxy_sku(is_active=1):
	# asin left unset so Proxy SKU.validate's duplicate-active check never trips.
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.is_active = is_active
	proxy_sku.insert()
	return proxy_sku.name


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
