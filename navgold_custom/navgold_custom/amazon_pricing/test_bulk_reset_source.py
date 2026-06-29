# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.fba_fee_fetcher import FEE_COST_SUB_TYPE, FEE_SOURCE
from navgold_custom.navgold_custom.doctype.bulk_reset_purchase_cost_source.bulk_reset_purchase_cost_source import (
	EXPLICIT_TARGET_SOURCES,
	build_reset_filters,
	enqueue_fba_fee_refresh,
	group_updates_by_target,
)


class TestBulkResetTargetSources(IntegrationTestCase):
	def test_item_tax_sync_is_a_valid_target(self):
		self.assertIn("Item-Tax-Sync", EXPLICIT_TARGET_SOURCES)

	def test_explicit_target_routes_all_rows_to_itself(self):
		rows = [
			{"cost_name": "C1", "cost_sub_type": "VAT-Standard"},
			{"cost_name": "C2", "cost_sub_type": "VAT-Standard"},
		]
		grouped = group_updates_by_target(rows, "Item-Tax-Sync")
		self.assertEqual(grouped, {"Item-Tax-Sync": ["C1", "C2"]})

	def test_auto_routes_vat_cost_type_to_item_tax_sync(self):
		rows = [
			{"cost_name": "C1", "cost_sub_type": "Tax Rate-Standard"},
			{"cost_name": "C2", "cost_sub_type": "Tax Rate-Exempt"},
		]
		grouped = group_updates_by_target(rows, "Auto", vat_cost_type=True)
		self.assertEqual(grouped, {"Item-Tax-Sync": ["C1", "C2"]})

	def test_auto_routes_non_vat_cost_type_by_sub_type(self):
		rows = [
			{"cost_name": "C1", "cost_sub_type": "Purchase Cost-Top Supplier"},
			{"cost_name": "C2", "cost_sub_type": "Purchase Cost-Warehouse Cost"},
			{"cost_name": "C3", "cost_sub_type": "Unmapped Sub Type"},
		]
		grouped = group_updates_by_target(rows, "Auto", vat_cost_type=False)
		self.assertEqual(
			grouped,
			{"Supplier-Sync": ["C1"], "PO-Sync": ["C2"], "Auto-Sync": ["C3"]},
		)

	def test_shipping_sync_is_a_valid_target(self):
		self.assertIn("Shipping-Sync", EXPLICIT_TARGET_SOURCES)

	def test_auto_routes_shipping_sub_types_to_shipping_sync(self):
		rows = [
			{"cost_name": "C1", "cost_sub_type": "Fulfillment Cost-Domestic Regular"},
			{"cost_name": "C2", "cost_sub_type": "Fulfillment Cost-Amazon Shipping"},
		]
		grouped = group_updates_by_target(rows, "Auto", vat_cost_type=False)
		self.assertEqual(grouped, {"Shipping-Sync": ["C1", "C2"]})

	def test_amazon_sync_is_a_valid_target(self):
		self.assertIn("Amazon-Sync", EXPLICIT_TARGET_SOURCES)

	def test_explicit_amazon_sync_target_routes_all_rows_to_itself(self):
		rows = [
			{"cost_name": "C1", "cost_sub_type": "Fulfillment Cost-Estimated FBA Fee"},
			{"cost_name": "C2", "cost_sub_type": "Fulfillment Cost-Estimated FBA Fee"},
		]
		grouped = group_updates_by_target(rows, "Amazon-Sync")
		self.assertEqual(grouped, {"Amazon-Sync": ["C1", "C2"]})

	def test_auto_routes_fba_fee_sub_type_to_amazon_sync(self):
		rows = [
			{"cost_name": "C1", "cost_sub_type": "Fulfillment Cost-Estimated FBA Fee"},
		]
		grouped = group_updates_by_target(rows, "Auto", vat_cost_type=False)
		self.assertEqual(grouped, {"Amazon-Sync": ["C1"]})

	def test_amazon_sync_target_restricts_filters_to_fba_fee_sub_type(self):
		doc = frappe._dict(
			target_source=FEE_SOURCE,
			cost_type="Fulfillment Cost",
			strategy_name=None,
			current_source=None,
			item=None,
			item_group=None,
			proxy_sku=None,
		)
		self.assertEqual(build_reset_filters(doc).get("cost_sub_type"), FEE_COST_SUB_TYPE)

	def test_other_targets_do_not_restrict_sub_type(self):
		doc = frappe._dict(
			target_source="Supplier-Sync",
			cost_type="Purchase Cost",
			strategy_name=None,
			current_source=None,
			item=None,
			item_group=None,
			proxy_sku=None,
		)
		self.assertNotIn("cost_sub_type", build_reset_filters(doc))

	def test_fba_fee_refresh_enqueues_the_fee_processor(self):
		with patch.object(frappe, "enqueue") as enqueue:
			enqueue_fba_fee_refresh()
		enqueue.assert_called_once()
		self.assertEqual(
			enqueue.call_args.args[0],
			"navgold_custom.navgold_custom.amazon_pricing.fba_fee_fetcher.process_fba_fee_fetch",
		)
