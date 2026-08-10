# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import make_item
from navgold_custom.navgold_custom.custom_script.material_request.material_request import (
	retire_settled_demand,
)
from navgold_custom.navgold_custom.custom_script.stock_entry.test_fba_clearing import (
	COMPANY,
	create_test_warehouse,
)
from navgold_custom.navgold_custom.erpnext_compat import make_delivery_note
from navgold_custom.patches.v1.retire_dead_material_request_demand import execute as run_backfill


class TestRetireDeadMaterialRequestDemand(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.warehouse = create_test_warehouse("Test Retire Demand")
		cls.customer = make_test_customer()

	def setUp(self):
		super().setUp()
		self.item_code = make_item(f"TEST-RETIRE-{self._testMethodName}", is_stock_item=1)

	def make_sales_order(self, qty=10, item_code=None):
		sales_order = frappe.new_doc("Sales Order")
		sales_order.customer = self.customer
		sales_order.company = COMPANY
		sales_order.transaction_date = nowdate()
		sales_order.delivery_date = add_days(nowdate(), 1)
		sales_order.append(
			"items",
			{
				"item_code": item_code or self.item_code,
				"qty": qty,
				"rate": 10,
				"warehouse": self.warehouse,
				"delivery_date": add_days(nowdate(), 1),
			},
		)
		sales_order.insert()
		sales_order.submit()
		return sales_order

	def make_material_request(self, sales_order=None, qty=10, item_code=None):
		material_request = frappe.new_doc("Material Request")
		material_request.company = COMPANY
		material_request.material_request_type = "Purchase"
		material_request.transaction_date = nowdate()
		material_request.schedule_date = add_days(nowdate(), 1)
		row = {
			"item_code": item_code or self.item_code,
			"qty": qty,
			"uom": "Nos",
			"warehouse": self.warehouse,
			"schedule_date": add_days(nowdate(), 1),
		}
		if sales_order:
			row["sales_order"] = sales_order.name
			row["sales_order_item"] = sales_order.items[0].name
		material_request.append("items", row)
		material_request.insert()
		material_request.submit()
		return material_request

	def receive_stock(self, qty, item_code=None):
		receipt = frappe.new_doc("Stock Entry")
		receipt.stock_entry_type = "Material Receipt"
		receipt.company = COMPANY
		receipt.append(
			"items",
			{
				"item_code": item_code or self.item_code,
				"qty": qty,
				"t_warehouse": self.warehouse,
				"basic_rate": 5,
			},
		)
		receipt.insert()
		receipt.submit()

	def deliver(self, sales_order):
		delivery_note = frappe.get_doc(make_delivery_note(sales_order.name))
		delivery_note.insert()
		delivery_note.submit()
		return delivery_note

	def status_of(self, material_request):
		return frappe.db.get_value("Material Request", material_request.name, "status")

	def test_delivering_the_order_retires_its_material_request(self):
		sales_order = self.make_sales_order()
		material_request = self.make_material_request(sales_order)
		self.assertEqual(self.status_of(material_request), "Pending")

		self.receive_stock(10)
		self.deliver(sales_order)

		self.assertEqual(self.status_of(material_request), "Stopped")

	def test_closing_the_order_retires_it_on_the_daily_sweep(self):
		sales_order = self.make_sales_order()
		material_request = self.make_material_request(sales_order)

		sales_order.update_status("Closed")
		self.assertEqual(self.status_of(material_request), "Pending")

		retire_settled_demand()

		self.assertEqual(self.status_of(material_request), "Stopped")

	def test_an_order_still_owed_keeps_its_material_request(self):
		sales_order = self.make_sales_order()
		material_request = self.make_material_request(sales_order)

		retire_settled_demand()

		self.assertEqual(self.status_of(material_request), "Pending")
		self.assertEqual(sales_order.status, "To Deliver and Bill")

	def test_a_part_delivered_order_keeps_its_material_request(self):
		sales_order = self.make_sales_order(qty=10)
		material_request = self.make_material_request(sales_order)

		self.receive_stock(10)
		delivery_note = frappe.get_doc(make_delivery_note(sales_order.name))
		delivery_note.items[0].qty = 4
		delivery_note.insert()
		delivery_note.submit()

		self.assertEqual(self.status_of(material_request), "Pending")

	def test_a_material_request_nobody_ordered_is_never_retired(self):
		material_request = self.make_material_request()

		retire_settled_demand()

		self.assertEqual(self.status_of(material_request), "Pending")

	def test_one_live_line_keeps_the_whole_material_request(self):
		settled_order = self.make_sales_order()
		live_item = make_item(f"TEST-RETIRE-{self._testMethodName}-live", is_stock_item=1)
		live_order = self.make_sales_order(item_code=live_item)

		material_request = frappe.new_doc("Material Request")
		material_request.company = COMPANY
		material_request.material_request_type = "Purchase"
		material_request.transaction_date = nowdate()
		material_request.schedule_date = add_days(nowdate(), 1)
		for order, item_code in ((settled_order, self.item_code), (live_order, live_item)):
			material_request.append(
				"items",
				{
					"item_code": item_code,
					"qty": 10,
					"uom": "Nos",
					"warehouse": self.warehouse,
					"schedule_date": add_days(nowdate(), 1),
					"sales_order": order.name,
					"sales_order_item": order.items[0].name,
				},
			)
		material_request.insert()
		material_request.submit()

		settled_order.update_status("Closed")
		retire_settled_demand()

		self.assertEqual(self.status_of(material_request), "Pending")

	def test_the_backfill_retires_demand_stranded_before_the_fix(self):
		sales_order = self.make_sales_order()
		material_request = self.make_material_request(sales_order)
		sales_order.update_status("Closed")
		self.assertEqual(self.status_of(material_request), "Pending")

		run_backfill()

		self.assertEqual(self.status_of(material_request), "Stopped")


def make_test_customer():
	name = "Test Retire Demand Customer"
	if frappe.db.exists("Customer", name):
		return name
	customer = frappe.new_doc("Customer")
	customer.customer_name = name
	customer.customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
	customer.territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
	customer.insert()
	return customer.name
