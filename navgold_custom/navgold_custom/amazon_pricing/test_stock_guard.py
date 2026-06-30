# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
	apply_stock_guard,
	get_sku_inventory_data,
)


class TestStockGuard(IntegrationTestCase):
	def test_reserve_pct_reduces_own_qty(self):
		self.assertEqual(apply_stock_guard(100, 10, 0), 90)

	def test_max_cap_applies(self):
		self.assertEqual(apply_stock_guard(90, 0, 50), 50)

	def test_reserve_and_cap_together(self):
		# 100 -> floor(100 * 0.9) = 90 -> capped at 50
		self.assertEqual(apply_stock_guard(100, 10, 50), 50)

	def test_defaults_are_noop(self):
		self.assertEqual(apply_stock_guard(100, 0, 0), 100)

	def test_reserve_floors_down(self):
		# floor(33 * 0.85) = floor(28.05) = 28
		self.assertEqual(apply_stock_guard(33, 15, 0), 28)

	def test_cap_above_qty_is_noop(self):
		self.assertEqual(apply_stock_guard(40, 0, 100), 40)

	def build_inventory(self, pricing_overrides, reserve_pct=0, max_publish_qty=0, mfn_stock=100):
		pricing = frappe._dict(
			{
				"proxy_sku": "GUARD-TEST-SKU",
				"active_strategy": "default",
				"fba_strategy": "fba",
				"sfp_strategy": "sfp",
				"backorder_strategy": "backorder",
			}
		)
		pricing.update(pricing_overrides)

		# pool_size=1, pack_size=1 so own warehouse_qty == mfn_stock.
		proxy_sku_info = {
			"GUARD-TEST-SKU": {
				"item": "GUARD-TEST-ITEM",
				"uom": "Nos",
				"pool_size": 1,
				"pack_size": 1.0,
				"merchant_shipping_group": None,
			}
		}
		stock_info_map = {"GUARD-TEST-SKU": {"mfn_stock": mfn_stock, "fba_stock": 0}}
		supplier_data_map = {
			"GUARD-TEST-ITEM": {
				"top_supplier": frappe._dict(supplier="S1", stock_qty=70, rank=1, price=5),
				"in_stock_supplier": frappe._dict(supplier="S1", stock_qty=70, rank=1, price=5),
			}
		}
		lead_time_map = {"S1": 1}
		# Avoid get_buildable_bundle_qty_map DB calls: a non-bundle item is absent from the map,
		# and the cap only fires when the map returns a value, so an empty map is a clean no-op.
		bundle_buildable_map = {}

		return get_sku_inventory_data(
			pricing,
			None,
			proxy_sku_info,
			stock_info_map,
			supplier_data_map,
			lead_time_map,
			bundle_buildable_map,
			reserve_pct,
			max_publish_qty,
		)

	def test_own_stock_reduced_via_inventory_data(self):
		inventory = self.build_inventory({"active_strategy": "default"}, reserve_pct=10, mfn_stock=100)
		self.assertEqual(inventory["quantity"], 90)

	def test_own_stock_capped_via_inventory_data(self):
		inventory = self.build_inventory(
			{"active_strategy": "default"}, reserve_pct=10, max_publish_qty=50, mfn_stock=100
		)
		self.assertEqual(inventory["quantity"], 50)

	def test_own_stock_defaults_unchanged_via_inventory_data(self):
		inventory = self.build_inventory({"active_strategy": "default"}, mfn_stock=100)
		self.assertEqual(inventory["quantity"], 100)

	def test_backorder_qty_not_reduced_by_guard(self):
		# Backorder publishes supplier stock (70); the guard must leave it untouched.
		inventory = self.build_inventory(
			{"active_strategy": "backorder"}, reserve_pct=50, max_publish_qty=10, mfn_stock=100
		)
		self.assertEqual(inventory["quantity"], 70)
