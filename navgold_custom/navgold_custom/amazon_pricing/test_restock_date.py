# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import get_sku_inventory_data
from navgold_custom.navgold_custom.amazon_pricing.test_sfp_backorder_item_optout import make_supplier
from navgold_custom.navgold_custom.custom_script.pick_list.test_delivery_consolidation import (
	get_test_company,
)
from navgold_custom.navgold_custom.utils import get_restock_data_map

RESTOCK_TEST_SUPPLIER = "RESTOCK-TEST-SUPPLIER"


class TestRestockDateFromOpenLines(IntegrationTestCase):
	def make_purchase_order(self, item_code, schedule_date, qty, received_qty, status):
		purchase_order = frappe.new_doc("Purchase Order")
		purchase_order.supplier = self.supplier
		purchase_order.company = self.company
		purchase_order.transaction_date = min(getdate(schedule_date), getdate(today()))
		purchase_order.schedule_date = schedule_date
		purchase_order.append(
			"items",
			{
				"item_code": item_code,
				"qty": qty,
				"rate": 5,
				"schedule_date": schedule_date,
				"warehouse": self.warehouse,
			},
		)
		purchase_order.insert(ignore_permissions=True)
		purchase_order.submit()

		frappe.db.set_value("Purchase Order Item", purchase_order.items[0].name, "received_qty", received_qty)
		frappe.db.set_value("Purchase Order", purchase_order.name, "status", status)
		return purchase_order

	def setUp(self):
		self.company = get_test_company()
		self.warehouse = frappe.db.get_value("Warehouse", {"company": self.company, "is_group": 0}, "name")
		self.supplier = make_supplier(RESTOCK_TEST_SUPPLIER, False).name

		self.item_code = f"RESTOCK-TEST-{self._testMethodName}"[:140]
		if not frappe.db.exists("Item", self.item_code):
			item = frappe.new_doc("Item")
			item.item_code = self.item_code
			item.item_name = self.item_code
			item.item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
			item.stock_uom = "Nos"
			item.is_stock_item = 1
			item.insert(ignore_permissions=True)

	def test_fully_received_line_is_not_a_pending_restock(self):
		self.make_purchase_order(
			self.item_code, add_days(today(), -6), qty=10, received_qty=10, status="To Bill"
		)
		self.assertEqual(get_restock_data_map([self.item_code]), {})

	def test_partially_received_line_still_counts(self):
		due = add_days(today(), 5)
		self.make_purchase_order(self.item_code, due, qty=10, received_qty=4, status="To Receive and Bill")
		self.assertEqual(getdate(get_restock_data_map([self.item_code])[self.item_code]), getdate(due))

	def test_received_line_ignored_even_when_po_header_still_awaits_stock(self):
		received_due = add_days(today(), -6)
		self.make_purchase_order(
			self.item_code, received_due, qty=10, received_qty=10, status="To Receive and Bill"
		)
		self.assertEqual(get_restock_data_map([self.item_code]), {})


class TestRestockDateNeverPublishedInThePast(IntegrationTestCase):
	def build_inventory(self, restock_date, lead_time_days=3):
		pricing = frappe._dict(
			{
				"proxy_sku": "RESTOCK-TEST-SKU",
				"active_strategy": "backorder",
				"fba_strategy": "fba",
				"sfp_strategy": "sfp",
				"backorder_strategy": "backorder",
			}
		)
		proxy_sku_info = {
			"RESTOCK-TEST-SKU": {
				"item": "RESTOCK-TEST-ITEM",
				"uom": "Nos",
				"pool_size": 1,
				"pack_size": 1.0,
				"merchant_shipping_group": None,
			}
		}
		stock_info_map = {
			"RESTOCK-TEST-SKU": {
				"mfn_stock": 5,
				"fba_stock": 0,
				"has_restock_date": True,
				"restock_date": restock_date,
			}
		}
		supplier_data_map = {
			"RESTOCK-TEST-ITEM": {
				"top_supplier": frappe._dict(supplier="S1", stock_qty=70, rank=1, price=5),
				"in_stock_supplier": frappe._dict(supplier="S1", stock_qty=70, rank=1, price=5),
			}
		}
		return get_sku_inventory_data(
			pricing,
			None,
			proxy_sku_info,
			stock_info_map,
			supplier_data_map,
			{"S1": lead_time_days},
			{},
			0,
			0,
		)

	def test_past_po_date_is_replaced_by_supplier_eta(self):
		inventory = self.build_inventory(add_days(today(), -6), lead_time_days=3)
		self.assertEqual(getdate(inventory["restock_date"]), getdate(add_days(today(), 3)))

	def test_future_po_date_is_published_unchanged(self):
		due = add_days(today(), 20)
		inventory = self.build_inventory(due, lead_time_days=3)
		self.assertEqual(getdate(inventory["restock_date"]), getdate(due))

	def test_todays_po_date_is_treated_as_stale(self):
		inventory = self.build_inventory(today(), lead_time_days=2)
		self.assertEqual(getdate(inventory["restock_date"]), getdate(add_days(today(), 2)))

	def test_published_date_is_never_before_today(self):
		for offset in (-30, -6, -1, 0, 1, 20):
			inventory = self.build_inventory(add_days(today(), offset), lead_time_days=3)
			self.assertGreaterEqual(getdate(inventory["restock_date"]), getdate(today()))
