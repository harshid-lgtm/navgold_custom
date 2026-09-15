# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
	get_lead_time_batch,
	get_sku_inventory_data,
)
from navgold_custom.navgold_custom.amazon_pricing.test_sfp_backorder_item_optout import (
	emitted_fulfillment,
	make_supplier,
)
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

	def test_the_soonest_open_line_is_the_restock_date(self):
		"""Two deliveries outstanding: the item is back when the first one lands. Taking the later
		date promised the customer the worst of them."""
		soonest = add_days(today(), 4)
		self.make_purchase_order(
			self.item_code, soonest, qty=10, received_qty=0, status="To Receive and Bill"
		)
		self.make_purchase_order(
			self.item_code, add_days(today(), 26), qty=10, received_qty=0, status="To Receive and Bill"
		)

		self.assertEqual(getdate(get_restock_data_map([self.item_code])[self.item_code]), getdate(soonest))

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
				"active_strategy": "default",
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
				"mfn_stock": 0,
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
			{"S1": False},
			{},
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


class TestRestockDateOnlyWhenThereIsNothingToShip(IntegrationTestCase):
	"""CLIP182-DUPx4's shape: a Prime Warehouse listing offering 9 packs off its own shelf still
	carried restock_date 2026-09-30, because PUR-ORD-2026-00159 sat at 68 of 72 received and kept a
	pending restock alive. Amazon quotes the delivery promise from that date, so the listing said
	2 October while the stock was on the shelf."""

	def build_inventory(self, active_strategy, mfn_stock, pack_size=4.0):
		pricing = frappe._dict(
			{
				"proxy_sku": "RESTOCK-SHIP-SKU",
				"active_strategy": active_strategy,
				"fba_strategy": "fba",
				"sfp_strategy": "sfp",
				"backorder_strategy": "backorder",
			}
		)
		proxy_sku_info = {
			"RESTOCK-SHIP-SKU": {
				"item": "RESTOCK-SHIP-ITEM",
				"uom": "Pack of 4",
				"pool_size": 1,
				"pack_size": pack_size,
				"merchant_shipping_group": None,
			}
		}
		stock_info_map = {
			"RESTOCK-SHIP-SKU": {
				"mfn_stock": mfn_stock,
				"fba_stock": 0,
				"has_restock_date": True,
				"restock_date": add_days(today(), 23),
			}
		}
		supplier_data_map = {
			"RESTOCK-SHIP-ITEM": {
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
			{"S1": 3},
			{},
			0,
			0,
			None,
			{"RESTOCK-SHIP-SKU": int(mfn_stock // pack_size)},
		)

	def test_a_listing_shipping_off_the_shelf_publishes_no_restock_date(self):
		inventory = self.build_inventory("sfp", mfn_stock=36)

		self.assertEqual(inventory["quantity"], 9, "36 units at 4 to a pack is 9 packs")
		self.assertIsNone(
			inventory["restock_date"],
			"the stock is on the shelf, so an incoming delivery is no part of the promise",
		)

	def test_a_listing_with_nothing_on_the_shelf_still_publishes_one(self):
		inventory = self.build_inventory("sfp", mfn_stock=0)

		self.assertEqual(inventory["quantity"], 0)
		self.assertEqual(getdate(inventory["restock_date"]), getdate(add_days(today(), 23)))

	def test_a_backorder_still_publishes_one(self):
		inventory = self.build_inventory("backorder", mfn_stock=0)

		self.assertEqual(getdate(inventory["restock_date"]), getdate(add_days(today(), 3)))


class TestBackorderRestockDateFromSupplierLeadTime(IntegrationTestCase):
	"""APSL-11581: 18632-DUPx12 on its Prime Supplier backorder strategy published restock_date
	2026-09-16 at Tue 15 Sep 16:16, taken from an open purchase order. CLF's Tuesday-after-12 lead time
	is 3 days, so Amazon should have been told 2026-09-18."""

	FROZEN_NOW = "2026-09-15 16:00:00"
	SUPPLIER_ETA = "2026-09-18"

	def setUp(self):
		self.supplier = make_supplier("RESTOCK-LEAD-TIME-SUPPLIER", False).name
		frappe.db.set_value(
			"Supplier Lead Time",
			{"parent": self.supplier, "parenttype": "Supplier", "day": "Tuesday"},
			{"lead_time_before_12": 1, "lead_time_after_12": 3},
		)

	def build_inventory(self, active_strategy, restock_date, supplier_stock=51):
		pricing = frappe._dict(
			{
				"proxy_sku": "RESTOCK-LEAD-SKU",
				"active_strategy": active_strategy,
				"fba_strategy": "fba",
				"sfp_strategy": "sfp",
				"backorder_strategy": "backorder",
			}
		)
		proxy_sku_info = {
			"RESTOCK-LEAD-SKU": {
				"item": "RESTOCK-LEAD-ITEM",
				"uom": "Pack of 12",
				"pool_size": 1,
				"pack_size": 12.0,
				"merchant_shipping_group": None,
			}
		}
		stock_info_map = {
			"RESTOCK-LEAD-SKU": {
				"mfn_stock": 0,
				"fba_stock": 0,
				"has_restock_date": bool(restock_date),
				"restock_date": restock_date,
			}
		}
		supplier_row = frappe._dict(supplier=self.supplier, stock_qty=supplier_stock, rank=1, price=5, sfp=0)
		supplier_data_map = {
			"RESTOCK-LEAD-ITEM": {"top_supplier": supplier_row, "in_stock_supplier": supplier_row}
		}
		return get_sku_inventory_data(
			pricing,
			None,
			proxy_sku_info,
			stock_info_map,
			supplier_data_map,
			get_lead_time_batch([self.supplier]),
			{},
			0,
			0,
			{self.supplier: False},
			{},
		)

	def test_backorder_ignores_a_purchase_order_due_tomorrow(self):
		with self.freeze_time(self.FROZEN_NOW):
			inventory = self.build_inventory("backorder", "2026-09-16")

		self.assertEqual(inventory["restock_date"], self.SUPPLIER_ETA)
		self.assertEqual(inventory["lead_time_days"], 1)
		self.assertEqual(
			emitted_fulfillment(inventory)["lead_time_to_ship_max_days"],
			1,
			"the supplier lead time is already in restock_date, so handling time stays nominal",
		)

	def test_backorder_ignores_a_purchase_order_due_next_month(self):
		with self.freeze_time(self.FROZEN_NOW):
			inventory = self.build_inventory("backorder", "2026-10-15")

		self.assertEqual(inventory["restock_date"], self.SUPPLIER_ETA)
		self.assertEqual(inventory["lead_time_days"], 1)

	def test_backorder_without_purchase_order_dates_from_supplier_stock(self):
		with self.freeze_time(self.FROZEN_NOW):
			inventory = self.build_inventory("backorder", None)

		self.assertEqual(inventory["restock_date"], self.SUPPLIER_ETA)
		self.assertEqual(inventory["lead_time_days"], 1)

	def test_backorder_without_purchase_order_or_supplier_stock_sends_no_date(self):
		with self.freeze_time(self.FROZEN_NOW):
			inventory = self.build_inventory("backorder", None, supplier_stock=0)

		self.assertIsNone(inventory["restock_date"])

	def test_empty_non_backorder_listing_keeps_the_purchase_order_date(self):
		with self.freeze_time(self.FROZEN_NOW):
			inventory = self.build_inventory("sfp", "2026-10-15")

		self.assertEqual(inventory["quantity"], 0)
		self.assertEqual(inventory["restock_date"], "2026-10-15")
