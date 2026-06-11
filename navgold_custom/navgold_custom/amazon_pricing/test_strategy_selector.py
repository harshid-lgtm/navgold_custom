# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, change_settings

from navgold_custom.navgold_custom.amazon_pricing.strategy_selector import (
	get_stock_info,
	get_stock_info_batch,
	select_strategy,
)
from navgold_custom.navgold_custom.amazon_pricing.template_applicator import apply_template_to_proxy_sku
from navgold_custom.navgold_custom.amazon_pricing.test_template_applicator import create_strategy_template
from navgold_custom.navgold_custom.utils import ITEM_AFN_FIELD, get_fba_stock_map


def create_test_item():
	item = frappe.new_doc("Item")
	item.item_code = f"TEST-AFN-{frappe.generate_hash(length=8)}"
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 1
	item.insert()
	return item.name


def create_fba_fixture(template_name):
	item_code = create_test_item()
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.item = item_code
	proxy_sku.uom = "Nos"
	proxy_sku.is_fba = 1
	proxy_sku.insert()
	apply_template_to_proxy_sku(template_name, proxy_sku.name)
	return item_code, proxy_sku.name


class TestStrategySelectorAFNBridge(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.template_name = create_strategy_template()

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 1})
	def test_sync_on_fetched_stock_with_empty_bin_selects_fba(self):
		item_code, proxy_sku = create_fba_fixture(self.template_name)
		# Stock exists only in Amazon's report (the go-live failure mode): Bin is empty.
		frappe.db.set_value("Item", item_code, ITEM_AFN_FIELD, 5, update_modified=False)

		stock_info = get_stock_info(proxy_sku)
		self.assertEqual(stock_info["fba_stock"], 5)

		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": proxy_sku})
		self.assertEqual(select_strategy(pricing), pricing.fba_strategy)

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 1})
	def test_sync_on_batch_path_uses_fetched_stock(self):
		item_code, proxy_sku = create_fba_fixture(self.template_name)
		frappe.db.set_value("Item", item_code, ITEM_AFN_FIELD, 3, update_modified=False)

		stock_info_map = get_stock_info_batch([proxy_sku])
		self.assertEqual(stock_info_map[proxy_sku]["fba_stock"], 3)

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 0})
	def test_sync_off_keeps_bin_based_behavior(self):
		item_code, proxy_sku = create_fba_fixture(self.template_name)
		# With sync off, the fetched figure must be ignored: behavior is exactly Bin-based.
		frappe.db.set_value("Item", item_code, ITEM_AFN_FIELD, 5, update_modified=False)

		stock_info = get_stock_info(proxy_sku)
		self.assertEqual(stock_info["fba_stock"], 0)

		batch_info = get_stock_info_batch([proxy_sku])
		self.assertEqual(batch_info[proxy_sku]["fba_stock"], 0)

		# No stock anywhere and no restock date drops to the backorder strategy, never FBA.
		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": proxy_sku})
		self.assertEqual(select_strategy(pricing), pricing.backorder_strategy)

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 1})
	def test_sync_on_takes_max_of_bin_and_fetched(self):
		item_code = create_test_item()
		frappe.db.set_value("Item", item_code, ITEM_AFN_FIELD, 3, update_modified=False)

		# Bin holds in-flight FBA Transfers Amazon does not count as fulfillable yet.
		with patch(
			"navgold_custom.navgold_custom.utils.get_afn_warehouse_stock_map",
			return_value={item_code: 10.0},
		):
			stock_map = get_fba_stock_map([item_code], "Any Warehouse")
		self.assertEqual(stock_map[item_code], 10.0)

		frappe.db.set_value("Item", item_code, ITEM_AFN_FIELD, 12, update_modified=False)
		with patch(
			"navgold_custom.navgold_custom.utils.get_afn_warehouse_stock_map",
			return_value={item_code: 10.0},
		):
			stock_map = get_fba_stock_map([item_code], "Any Warehouse")
		self.assertEqual(stock_map[item_code], 12.0)
