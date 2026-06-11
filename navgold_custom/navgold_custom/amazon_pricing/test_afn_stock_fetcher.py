# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.afn_stock_fetcher import (
	compute_afn_stock_updates,
	compute_item_afn_totals,
)


class TestAFNStockFetcher(IntegrationTestCase):
	def test_compute_updates_present_sku_updated(self):
		current_rows = [{"name": "SKU-1", "afn_stock": 2}]
		value_map = {"SKU-1": 7.0}
		self.assertEqual(compute_afn_stock_updates(current_rows, value_map), {"SKU-1": {"afn_stock": 7.0}})

	def test_compute_updates_absent_sku_zeroed(self):
		# A row not present in a successful value_map is out of stock and must be zeroed.
		current_rows = [{"name": "SKU-1", "afn_stock": 5}]
		value_map = {"SKU-OTHER": 3.0}
		self.assertEqual(compute_afn_stock_updates(current_rows, value_map), {"SKU-1": {"afn_stock": 0.0}})

	def test_compute_updates_unchanged_value_omitted(self):
		current_rows = [{"name": "SKU-1", "afn_stock": 7}]
		value_map = {"SKU-1": 7.0}
		self.assertEqual(compute_afn_stock_updates(current_rows, value_map), {})

	def test_compute_updates_empty_map_zeroes_all_non_empty(self):
		current_rows = [
			{"name": "SKU-1", "afn_stock": 5},
			{"name": "SKU-2", "afn_stock": 0},
		]
		# SKU-2 already at 0 stays omitted; SKU-1 zeroed.
		self.assertEqual(compute_afn_stock_updates(current_rows, {}), {"SKU-1": {"afn_stock": 0.0}})

	def test_compute_updates_custom_field_name(self):
		current_rows = [{"name": "ITEM-1", "custom_amazon_afn_stock": 3}]
		value_map = {"ITEM-1": 12.0}
		self.assertEqual(
			compute_afn_stock_updates(current_rows, value_map, field="custom_amazon_afn_stock"),
			{"ITEM-1": {"custom_amazon_afn_stock": 12.0}},
		)

	def test_item_totals_two_skus_same_item_multiply_and_sum(self):
		proxy_rows = [
			{"name": "SKU-A", "item": "ITEM-1", "uom": "Box6"},
			{"name": "SKU-B", "item": "ITEM-1", "uom": "Nos"},
		]
		sku_qty_map = {"SKU-A": 2.0, "SKU-B": 5.0}
		factors = {("ITEM-1", "Box6"): 6.0, ("ITEM-1", "Nos"): 1.0}
		conversion = lambda item, uom: factors[(item, uom)]  # noqa: E731
		# 2 boxes of 6 = 12 base units + 5 singles = 17 total in stock UOM.
		self.assertEqual(compute_item_afn_totals(proxy_rows, sku_qty_map, conversion), {"ITEM-1": 17.0})

	def test_item_totals_missing_sku_contributes_zero(self):
		proxy_rows = [
			{"name": "SKU-A", "item": "ITEM-1", "uom": "Box6"},
			{"name": "SKU-B", "item": "ITEM-1", "uom": "Nos"},
		]
		# SKU-B not returned by Amazon → 0, only SKU-A contributes.
		sku_qty_map = {"SKU-A": 3.0}
		factors = {("ITEM-1", "Box6"): 6.0, ("ITEM-1", "Nos"): 1.0}
		conversion = lambda item, uom: factors[(item, uom)]  # noqa: E731
		self.assertEqual(compute_item_afn_totals(proxy_rows, sku_qty_map, conversion), {"ITEM-1": 18.0})

	def test_item_totals_all_skus_zero_yields_zero_total(self):
		proxy_rows = [{"name": "SKU-A", "item": "ITEM-1", "uom": "Box6"}]
		sku_qty_map = {"SKU-A": 0.0}
		conversion = lambda item, uom: 6.0  # noqa: E731
		# An FBA item whose SKUs all dropped to 0 must produce a 0 total so it gets zeroed.
		self.assertEqual(compute_item_afn_totals(proxy_rows, sku_qty_map, conversion), {"ITEM-1": 0.0})

	def test_item_totals_distinct_items_kept_separate(self):
		proxy_rows = [
			{"name": "SKU-A", "item": "ITEM-1", "uom": "Nos"},
			{"name": "SKU-B", "item": "ITEM-2", "uom": "Nos"},
		]
		sku_qty_map = {"SKU-A": 4.0, "SKU-B": 9.0}
		conversion = lambda item, uom: 1.0  # noqa: E731
		self.assertEqual(
			compute_item_afn_totals(proxy_rows, sku_qty_map, conversion),
			{"ITEM-1": 4.0, "ITEM-2": 9.0},
		)
