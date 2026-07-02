# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.data import flt

from navgold_custom.navgold_custom.amazon_pricing.purchase_cost_sources import (
	calculate_bundle_cost,
	log_missing_bundle_component_rates,
)
from navgold_custom.navgold_custom.doctype.amazon_product_strategy.amazon_product_strategy import (
	refresh_strategy_costs_chunk,
)

BUNDLE_ITEM_CODE = "TEST-STRATEGY-BUNDLE"
COMPONENT_ITEM_CODE = "TEST-STRATEGY-COMPONENT"
COST_TYPE_NAME = "Test Bundle Purchase Cost"
CONSOLIDATED_TITLE_LIKE = "Bundle purchase cost skipped%"


def create_item(item_code, is_stock_item):
	if frappe.db.exists("Item", item_code):
		return item_code

	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = item_code
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = is_stock_item
	item.insert()
	return item.name


def create_product_bundle():
	if frappe.db.exists("Product Bundle", {"new_item_code": BUNDLE_ITEM_CODE}):
		return

	bundle = frappe.new_doc("Product Bundle")
	bundle.new_item_code = BUNDLE_ITEM_CODE
	bundle.is_active = 1
	bundle.append(
		"items",
		{
			"item_code": COMPONENT_ITEM_CODE,
			"qty": 2,
			"uom": "Nos",
			"description": COMPONENT_ITEM_CODE,
		},
	)
	bundle.insert()
	# get_product_bundle_items only counts submitted bundles on erpnext v17+
	if bundle.meta.is_submittable and bundle.docstatus == 0:
		bundle.submit()


def create_cost_sub_type():
	if not frappe.db.exists("Cost Type", COST_TYPE_NAME):
		cost_type = frappe.new_doc("Cost Type")
		cost_type.cost_type_name = COST_TYPE_NAME
		cost_type.calculation_type = "Absolute"
		cost_type.is_purchase_cost = 1
		cost_type.insert()

	sub_type_name = f"{COST_TYPE_NAME}-Supplier"
	if not frappe.db.exists("Cost Sub Type", sub_type_name):
		sub_type = frappe.new_doc("Cost Sub Type")
		sub_type.cost_type = COST_TYPE_NAME
		sub_type.sub_type_name = "Supplier"
		sub_type.source = "Supplier-Sync"
		sub_type.insert()
	return sub_type_name


def create_proxy_sku():
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.item = BUNDLE_ITEM_CODE
	proxy_sku.uom = "Nos"
	proxy_sku.is_active = 1
	proxy_sku.insert()
	return proxy_sku.name


def create_strategy(proxy_sku_name, strategy_name, cost_sub_type_name):
	strategy = frappe.new_doc("Amazon Product Strategy")
	strategy.proxy_sku = proxy_sku_name
	strategy.strategy_name = strategy_name
	strategy.is_active = 1
	strategy.min_margin = 5
	strategy.max_margin = 10
	strategy.append(
		"costs",
		{
			"cost_type": COST_TYPE_NAME,
			"cost_sub_type": cost_sub_type_name,
			"value_mode": "Auto",
			"rank": 1,
			"value": 0,
		},
	)
	strategy.insert()
	return strategy.name


class TestBundleRateLogging(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_item(COMPONENT_ITEM_CODE, is_stock_item=1)
		# Product Bundle parents must be non-stock items
		create_item(BUNDLE_ITEM_CODE, is_stock_item=0)
		create_product_bundle()
		cls.cost_sub_type_name = create_cost_sub_type()
		cls.proxy_sku_name = create_proxy_sku()
		cls.strategy_name = create_strategy(cls.proxy_sku_name, "Default", cls.cost_sub_type_name)
		cls.second_strategy_name = create_strategy(cls.proxy_sku_name, "FBA", cls.cost_sub_type_name)

	def test_refresh_chunk_emits_single_consolidated_error_log(self):
		frappe.db.delete("Error Log")

		# Both strategies hit the same bundle with a missing component rate;
		# the whole run must still produce exactly one Error Log.
		result = refresh_strategy_costs_chunk([self.strategy_name, self.second_strategy_name])

		self.assertEqual(result["refreshed"], 2)
		self.assertEqual(frappe.db.count("Error Log"), 1)

		consolidated_logs = frappe.get_all(
			"Error Log",
			filters={"method": ["like", CONSOLIDATED_TITLE_LIKE]},
			fields=["method", "error"],
		)
		self.assertEqual(len(consolidated_logs), 1)
		self.assertIn(BUNDLE_ITEM_CODE, consolidated_logs[0].error)
		self.assertIn(COMPONENT_ITEM_CODE, consolidated_logs[0].error)

	def test_bundle_with_missing_component_rate_still_skipped(self):
		refresh_strategy_costs_chunk([self.strategy_name])

		supplier_sync_value = frappe.db.get_value(
			"Amazon Product Cost",
			{"parent": self.strategy_name, "cost_sub_type": self.cost_sub_type_name},
			"value",
		)
		self.assertEqual(flt(supplier_sync_value), 0.0)

	def test_interactive_save_does_not_log_per_bundle(self):
		frappe.db.delete("Error Log")

		strategy = frappe.get_doc("Amazon Product Strategy", self.strategy_name)
		strategy.save(ignore_permissions=True)

		self.assertEqual(frappe.db.count("Error Log"), 0)

	def test_calculate_bundle_cost_collects_misses_without_logging(self):
		frappe.db.delete("Error Log")

		missing_component_rates = []
		total = calculate_bundle_cost(
			"BUNDLE-X",
			[{"item_code": "COMP-X", "qty": 2}],
			lambda item_codes: {},
			missing_component_rates,
		)

		self.assertEqual(total, 0.0)
		self.assertEqual(missing_component_rates, [("BUNDLE-X", "COMP-X")])
		self.assertEqual(frappe.db.count("Error Log"), 0)

	def test_log_missing_bundle_component_rates_dedupes_pairs(self):
		frappe.db.delete("Error Log")

		log_missing_bundle_component_rates(
			[("BUNDLE-X", "COMP-X"), ("BUNDLE-X", "COMP-X"), ("BUNDLE-Y", "COMP-Y")]
		)

		error_logs = frappe.get_all(
			"Error Log",
			filters={"method": ["like", CONSOLIDATED_TITLE_LIKE]},
			fields=["method", "error"],
		)
		self.assertEqual(len(error_logs), 1)
		self.assertIn("2 bundles", error_logs[0].method)
		self.assertEqual(error_logs[0].error.count("BUNDLE-X"), 1)

	def test_log_missing_bundle_component_rates_skips_empty(self):
		frappe.db.delete("Error Log")

		log_missing_bundle_component_rates([])

		self.assertEqual(frappe.db.count("Error Log"), 0)
