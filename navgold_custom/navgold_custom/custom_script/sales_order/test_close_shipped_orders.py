# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, nowdate

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import make_item
from navgold_custom.navgold_custom.custom_script.material_request.test_retire_dead_demand import (
	make_test_customer,
)
from navgold_custom.navgold_custom.custom_script.sales_order.sales_order import (
	close_shipped_orders_without_delivery,
	reconcile_shipped_draft_delivery_notes,
)
from navgold_custom.navgold_custom.custom_script.stock_entry.test_fba_clearing import (
	COMPANY,
	create_test_warehouse,
)
from navgold_custom.navgold_custom.erpnext_compat import make_delivery_note


class TestCloseShippedOrdersWithoutDelivery(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.warehouse = create_test_warehouse("Test Close Shipped")
		cls.customer = make_test_customer()

	def setUp(self):
		super().setUp()
		self.item_code = make_item(f"TEST-CLOSE-SHIPPED-{self._testMethodName}", is_stock_item=1)

	def make_sales_order(self, qty, amazon_order_status="Shipped"):
		sales_order = frappe.new_doc("Sales Order")
		sales_order.customer = self.customer
		sales_order.company = COMPANY
		sales_order.fulfillment_channel = "MFN"
		sales_order.amazon_order_status = amazon_order_status
		sales_order.transaction_date = nowdate()
		sales_order.delivery_date = add_days(nowdate(), 3)
		sales_order.append(
			"items",
			{
				"item_code": self.item_code,
				"qty": qty,
				"uom": "Nos",
				"rate": 10,
				"warehouse": self.warehouse,
				"delivery_date": add_days(nowdate(), 3),
			},
		)
		sales_order.insert()
		sales_order.submit()
		return sales_order

	def receive_stock(self, qty):
		receipt = frappe.new_doc("Stock Entry")
		receipt.stock_entry_type = "Material Receipt"
		receipt.company = COMPANY
		receipt.append(
			"items",
			{"item_code": self.item_code, "qty": qty, "t_warehouse": self.warehouse, "basic_rate": 5},
		)
		receipt.insert()
		receipt.submit()

	def make_open_pick_list(self, sales_order, qty):
		pick_list = frappe.new_doc("Pick List")
		pick_list.company = COMPANY
		pick_list.purpose = "Delivery"
		pick_list.append(
			"locations",
			{
				"item_code": self.item_code,
				"qty": qty,
				"stock_qty": qty,
				"picked_qty": qty,
				"uom": "Nos",
				"conversion_factor": 1,
				"warehouse": self.warehouse,
				"sales_order": sales_order.name,
				"sales_order_item": sales_order.items[0].name,
			},
		)
		pick_list.insert()
		frappe.db.set_value("Pick List", pick_list.name, {"docstatus": 1, "status": "Open"})
		return pick_list

	def status_of(self, sales_order):
		return frappe.db.get_value("Sales Order", sales_order.name, "status")

	def reserved_qty(self):
		return flt(
			frappe.db.get_value(
				"Bin", {"item_code": self.item_code, "warehouse": self.warehouse}, "reserved_qty"
			)
		)

	def request_status_for(self, sales_order):
		parent = frappe.db.get_value(
			"Material Request Item", {"sales_order": sales_order.name, "docstatus": 1}, "parent"
		)
		return frappe.db.get_value("Material Request", parent, "status")

	def test_a_shipped_order_with_no_delivery_note_is_closed(self):
		sales_order = self.make_sales_order(qty=2)
		self.assertEqual(self.reserved_qty(), 2)

		closed = close_shipped_orders_without_delivery()

		self.assertIn(sales_order.name, closed)
		self.assertEqual(self.status_of(sales_order), "Closed")
		self.assertEqual(self.reserved_qty(), 0, "closing is what hands the reservation back")
		self.assertEqual(self.request_status_for(sales_order), "Stopped")

	def test_an_unshipped_order_is_left_open(self):
		sales_order = self.make_sales_order(qty=2, amazon_order_status="Unshipped")

		close_shipped_orders_without_delivery()

		self.assertEqual(self.status_of(sales_order), "To Deliver and Bill")

	def test_a_draft_delivery_note_without_open_pick_list_is_left_open(self):
		sales_order = self.make_sales_order(qty=2)
		make_delivery_note(sales_order.name).insert()

		close_shipped_orders_without_delivery()

		self.assertEqual(
			self.status_of(sales_order),
			"To Deliver and Bill",
			"a standalone Draft Delivery Note is not enough evidence to auto-submit",
		)

	def test_shipped_order_with_open_pick_list_recovers_draft_delivery_note(self):
		self.receive_stock(2)
		sales_order = self.make_sales_order(qty=2)
		self.make_open_pick_list(sales_order, 2)
		delivery_note = make_delivery_note(sales_order.name)
		delivery_note.insert()

		recovered = reconcile_shipped_draft_delivery_notes([sales_order.name])

		self.assertIn(delivery_note.name, recovered)
		self.assertEqual(frappe.db.get_value("Delivery Note", delivery_note.name, "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Sales Order", sales_order.name, "per_delivered"), 100)

	def test_unshipped_order_does_not_recover_draft_delivery_note(self):
		self.receive_stock(2)
		sales_order = self.make_sales_order(qty=2, amazon_order_status="Unshipped")
		self.make_open_pick_list(sales_order, 2)
		delivery_note = make_delivery_note(sales_order.name)
		delivery_note.insert()

		recovered = reconcile_shipped_draft_delivery_notes([])

		self.assertEqual(recovered, [])
		self.assertEqual(frappe.db.get_value("Delivery Note", delivery_note.name, "docstatus"), 0)

	def test_an_order_on_an_open_pick_list_is_left_open(self):
		self.receive_stock(2)
		sales_order = self.make_sales_order(qty=2)
		pick_list = frappe.new_doc("Pick List")
		pick_list.company = COMPANY
		pick_list.purpose = "Delivery"
		pick_list.append(
			"locations",
			{
				"item_code": self.item_code,
				"qty": 2,
				"stock_qty": 2,
				"uom": "Nos",
				"conversion_factor": 1,
				"warehouse": self.warehouse,
				"sales_order": sales_order.name,
				"sales_order_item": sales_order.items[0].name,
			},
		)
		pick_list.insert()

		close_shipped_orders_without_delivery()

		self.assertEqual(self.status_of(sales_order), "To Deliver and Bill")

	def test_a_partly_delivered_order_is_left_open(self):
		self.receive_stock(1)
		sales_order = self.make_sales_order(qty=2)
		delivery_note = make_delivery_note(sales_order.name)
		delivery_note.items[0].qty = 1
		delivery_note.insert()
		delivery_note.submit()

		close_shipped_orders_without_delivery()

		self.assertEqual(self.status_of(sales_order), "To Deliver and Bill")
