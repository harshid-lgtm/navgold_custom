# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.report.amazon_go_live_data_readiness.amazon_go_live_data_readiness import (
	get_purchase_cost_map,
)
from navgold_custom.navgold_custom.report.amazon_purchase_cost_overview.amazon_purchase_cost_overview import (
	fetch_active_purchase_cost_rows,
)

PURCHASE_GROUP = "Test Report Purchase Cost"
PURCHASE_LEAF = f"{PURCHASE_GROUP}-Landed"
ITEM_CODE = "TEST-REPORT-COST-ITEM"


def ensure_purchase_cost_type():
	if not frappe.db.exists("Cost Type", PURCHASE_GROUP):
		group = frappe.new_doc("Cost Type")
		group.cost_type_name = PURCHASE_GROUP
		group.is_group = 1
		group.calculation_type = "Absolute"
		group.is_purchase_cost = 1
		group.insert()
	if not frappe.db.exists("Cost Type", PURCHASE_LEAF):
		leaf = frappe.new_doc("Cost Type")
		leaf.cost_type_name = "Landed"
		leaf.is_group = 0
		leaf.parent_cost_type = PURCHASE_GROUP
		leaf.insert()


def ensure_item():
	if not frappe.db.exists("Item", ITEM_CODE):
		item = frappe.new_doc("Item")
		item.item_code = ITEM_CODE
		item.item_name = ITEM_CODE
		item.item_group = "All Item Groups"
		item.stock_uom = "Nos"
		item.is_stock_item = 0
		item.insert()


def build_strategy():
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.item = ITEM_CODE
	proxy_sku.uom = "Nos"
	proxy_sku.is_active = 1
	proxy_sku.insert()

	strategy = frappe.new_doc("Amazon Product Strategy")
	strategy.proxy_sku = proxy_sku.name
	strategy.strategy_name = "Default"
	strategy.is_active = 1
	strategy.min_margin = 5
	strategy.max_margin = 10
	strategy.append(
		"costs",
		{
			"cost_type": PURCHASE_GROUP,
			"cost_sub_type": PURCHASE_LEAF,
			"value": 5.0,
			"value_mode": "Manual",
			"rank": 1,
		},
	)
	strategy.insert()
	return strategy


class TestPurchaseCostReportsTree(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_purchase_cost_type()
		ensure_item()
		cls.strategy = build_strategy()

	def test_readiness_purchase_cost_map_reads_group(self):
		cost_map = get_purchase_cost_map([self.strategy.name])
		self.assertEqual(cost_map.get(self.strategy.name), 5.0)

	def test_overview_returns_active_purchase_cost_row(self):
		rows = fetch_active_purchase_cost_rows({"proxy_sku": self.strategy.proxy_sku}, [PURCHASE_GROUP])
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["cost_sub_type"], PURCHASE_LEAF)
		self.assertEqual(rows[0]["current_value"], 5.0)
