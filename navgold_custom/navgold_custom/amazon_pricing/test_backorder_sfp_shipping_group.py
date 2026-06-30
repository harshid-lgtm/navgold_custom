# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
	SHIPPING_GROUPS,
	get_sku_inventory_data,
)


def backorder_pricing():
	return frappe._dict(
		proxy_sku="TEST-PROXY-SKU",
		active_strategy="TEST-STRAT-BACKORDER",
		fba_strategy="TEST-STRAT-FBA",
		sfp_strategy="TEST-STRAT-SFP",
		backorder_strategy="TEST-STRAT-BACKORDER",
	)


def inventory_for_backorder(supplier_sfp_eligible, has_restock_date):
	proxy_sku_info = {
		"TEST-PROXY-SKU": {
			"item": "TEST-ITEM",
			"uom": "Nos",
			"pool_size": 1,
			"pack_size": 1.0,
			"merchant_shipping_group": None,
		}
	}
	stock_info_map = {
		"TEST-PROXY-SKU": {
			"mfn_stock": 0,
			"has_restock_date": has_restock_date,
			"restock_date": "2026-07-10" if has_restock_date else None,
		}
	}
	resolved_supplier = frappe._dict(supplier="TEST-SUPPLIER", stock_qty=5, rank=1, price=10, sfp=1)
	supplier_data_map = {
		"TEST-ITEM": {"top_supplier": resolved_supplier, "in_stock_supplier": resolved_supplier}
	}

	return get_sku_inventory_data(
		backorder_pricing(),
		zero_stock_proxy_skus=None,
		proxy_sku_info=proxy_sku_info,
		stock_info_map=stock_info_map,
		supplier_data_map=supplier_data_map,
		lead_time_map={"TEST-SUPPLIER": 2},
		bundle_buildable_map={},
		supplier_sfp_eligible_map={"TEST-SUPPLIER": supplier_sfp_eligible},
	)


class TestBackorderSfpShippingGroup(IntegrationTestCase):
	def test_eligible_supplier_with_restock_date_lists_as_sfp(self):
		inventory = inventory_for_backorder(supplier_sfp_eligible=True, has_restock_date=True)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["sfp"])
		# A backorder-SFP still asserts its restock date and lead time.
		self.assertTrue(inventory["send_lead_time"])
		self.assertIsNotNone(inventory["restock_date"])

	def test_eligible_supplier_without_restock_date_stays_main_revised(self):
		inventory = inventory_for_backorder(supplier_sfp_eligible=True, has_restock_date=False)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["main_revised"])

	def test_ineligible_supplier_with_restock_date_stays_main_revised(self):
		inventory = inventory_for_backorder(supplier_sfp_eligible=False, has_restock_date=True)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["main_revised"])
