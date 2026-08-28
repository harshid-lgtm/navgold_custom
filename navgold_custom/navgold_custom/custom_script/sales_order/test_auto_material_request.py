# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, nowdate

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import make_item
from navgold_custom.navgold_custom.custom_script.material_request.test_retire_dead_demand import (
	make_test_customer,
)
from navgold_custom.navgold_custom.custom_script.stock_entry.test_fba_clearing import (
	COMPANY,
	create_test_warehouse,
)


class TestAutoMaterialRequest(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.warehouse = create_test_warehouse("Test Auto MR")
		cls.customer = make_test_customer()

	def setUp(self):
		super().setUp()
		self.item_code = make_item(f"TEST-AUTO-MR-{self._testMethodName}", is_stock_item=1)

	def make_sales_order(self, lines, fulfillment_channel="MFN"):
		sales_order = frappe.new_doc("Sales Order")
		sales_order.customer = self.customer
		sales_order.company = COMPANY
		sales_order.fulfillment_channel = fulfillment_channel
		sales_order.transaction_date = nowdate()
		sales_order.delivery_date = add_days(nowdate(), 1)
		for qty, uom in lines:
			sales_order.append(
				"items",
				{
					"item_code": self.item_code,
					"qty": qty,
					"uom": uom,
					"rate": 10,
					"warehouse": self.warehouse,
					"delivery_date": add_days(nowdate(), 1),
				},
			)
		sales_order.insert()
		sales_order.submit()
		return sales_order

	def strand_demand(self, qty):
		"""Reserve stock through an order that raises no request of its own, so the item carries a
		shortage larger than the next order's own line — the state every failed request left behind."""
		return self.make_sales_order([(qty, "Nos")], fulfillment_channel="")

	def add_pack_uom(self, uom, conversion_factor):
		item = frappe.get_doc("Item", self.item_code)
		item.append("uoms", {"uom": uom, "conversion_factor": conversion_factor})
		item.save()

	def request_for(self, sales_order):
		names = frappe.get_all(
			"Material Request Item",
			filters={"sales_order": sales_order.name, "docstatus": 1},
			pluck="parent",
			distinct=True,
		)
		return frappe.get_doc("Material Request", names[0]) if names else None

	def test_request_is_capped_at_its_own_sales_order_line(self):
		self.strand_demand(20)

		sales_order = self.make_sales_order([(5, "Nos")])

		material_request = self.request_for(sales_order)
		self.assertIsNotNone(material_request, "no request raised — the item-wide shortage was rejected")
		self.assertEqual(flt(material_request.items[0].stock_qty), 5)

	def test_two_lines_of_one_item_split_the_shortage(self):
		self.strand_demand(20)

		sales_order = self.make_sales_order([(4, "Nos"), (3, "Nos")])

		material_request = self.request_for(sales_order)
		self.assertIsNotNone(material_request)
		self.assertEqual(
			sorted(flt(row.stock_qty) for row in material_request.items),
			[3.0, 4.0],
		)

	def test_a_pack_line_is_requested_in_loose_units(self):
		self.add_pack_uom("Pack of 6", 6)

		sales_order = self.make_sales_order([(2, "Pack of 6")])
		self.assertEqual(flt(sales_order.items[0].stock_qty), 12)

		material_request = self.request_for(sales_order)
		self.assertIsNotNone(material_request)
		self.assertEqual(flt(material_request.items[0].conversion_factor), 1)
		self.assertEqual(flt(material_request.items[0].stock_qty), 12)

	def test_no_request_when_stock_covers_the_order(self):
		self.receive_stock(10)

		sales_order = self.make_sales_order([(5, "Nos")])

		self.assertIsNone(self.request_for(sales_order))

	def receive_stock(self, qty):
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.stock_entry_type = "Material Receipt"
		stock_entry.company = COMPANY
		stock_entry.append(
			"items",
			{
				"item_code": self.item_code,
				"qty": qty,
				"t_warehouse": self.warehouse,
				"basic_rate": 5,
			},
		)
		stock_entry.insert()
		stock_entry.submit()
		return stock_entry
