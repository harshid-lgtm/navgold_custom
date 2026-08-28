# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, nowdate

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import make_item
from navgold_custom.navgold_custom.custom_script.stock_entry.test_fba_clearing import (
	COMPANY,
	create_test_warehouse,
)
from navgold_custom.navgold_custom.report.navgold_forecast_report.test_navgold_forecast_report import (
	add_uom_conversion,
	make_supplier,
	make_supplier_price,
)
from navgold_custom.navgold_custom.report.to_purchase.to_purchase import (
	create_purchase_orders_from_report,
)

PACK_UOM = "Pack of 6"
PACK_SIZE = 6


class TestToPurchasePurchaseOrders(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.warehouse = create_test_warehouse("Test To Purchase")
		cls.supplier = make_supplier(f"NG To Purchase Supplier {frappe.generate_hash(length=6)}")

	def setUp(self):
		super().setUp()
		self.use_test_warehouse()
		self.drop_over_limit_bypass_role()
		self.item_code = make_item(f"TEST-TO-PURCHASE-{self._testMethodName}", is_stock_item=1)
		add_uom_conversion(self.item_code, PACK_UOM, PACK_SIZE)
		make_supplier_price(self.item_code, self.supplier, price=1, uom=PACK_UOM)

	def use_test_warehouse(self):
		"""Point the Purchase Order target at the test warehouse. change_settings cannot do it:
		Amazon SP API Settings overrides save() without kwargs and rejects ignore_permissions."""
		for field, value in (("warehouse", self.warehouse), ("company", COMPANY)):
			previous = frappe.db.get_single_value("Amazon SP API Settings", field)
			frappe.db.set_single_value("Amazon SP API Settings", field, value)
			self.addCleanup(frappe.db.set_single_value, "Amazon SP API Settings", field, previous)

	def drop_over_limit_bypass_role(self):
		"""Buyers hit the over-limit throw; Administrator, who runs the tests, holds the bypass role
		and would only be warned. Clearing it puts the test on the buyer's side of that branch."""
		previous = frappe.db.get_single_value("Stock Settings", "role_allowed_to_over_deliver_receive")
		frappe.db.set_single_value("Stock Settings", "role_allowed_to_over_deliver_receive", "")
		self.addCleanup(
			frappe.db.set_single_value,
			"Stock Settings",
			"role_allowed_to_over_deliver_receive",
			previous,
		)

	def make_material_request(self, qty):
		material_request = frappe.new_doc("Material Request")
		material_request.company = COMPANY
		material_request.material_request_type = "Purchase"
		material_request.transaction_date = nowdate()
		material_request.schedule_date = add_days(nowdate(), 1)
		material_request.append(
			"items",
			{
				"item_code": self.item_code,
				"qty": qty,
				"uom": "Nos",
				"conversion_factor": 1,
				"warehouse": self.warehouse,
				"schedule_date": add_days(nowdate(), 1),
			},
		)
		material_request.insert()
		material_request.submit()
		return material_request

	def report_row(self, allocated_qty, supplier_uom=PACK_UOM):
		return {
			"item_code": self.item_code,
			"supplier": self.supplier,
			"supplier_uom": supplier_uom,
			"available_stock": 100,
			"allocated_qty": allocated_qty,
			"unit_price": 1,
		}

	def test_a_purchase_order_rounds_demand_up_to_a_whole_supplier_pack(self):
		# 92 is not a multiple of 6, so the order has to reach for a 16th pack and ends up 4 units
		# past what the request asked for — the shape that used to abort the whole batch.
		material_request = self.make_material_request(92)

		created = create_purchase_orders_from_report([self.report_row(92)])

		self.assertEqual(len(created["purchase_orders"]), 1)

		purchase_order = frappe.get_doc("Purchase Order", created["purchase_orders"][0])
		self.assertEqual(purchase_order.docstatus, 1)
		self.assertEqual(flt(purchase_order.items[0].qty), 16)
		self.assertEqual(flt(purchase_order.items[0].stock_qty), 96)
		self.assertEqual(purchase_order.items[0].material_request, material_request.name)

		material_request.reload()
		self.assertEqual(flt(material_request.items[0].ordered_qty), 96)

	def test_pack_aligned_demand_orders_exactly_what_was_asked_for(self):
		self.make_material_request(90)

		created = create_purchase_orders_from_report([self.report_row(90)])

		purchase_order = frappe.get_doc("Purchase Order", created["purchase_orders"][0])
		self.assertEqual(flt(purchase_order.items[0].qty), 15)
		self.assertEqual(flt(purchase_order.items[0].stock_qty), 90)

	def test_one_unit_of_demand_still_buys_a_whole_pack(self):
		# The worst the rounding gets: a single loose unit reaches for a full pack of twelve, which
		# reads as 1100% over the request. No percentage tuned to a "realistic" overshoot survives it.
		large_pack = "Pack of 12"
		add_uom_conversion(self.item_code, large_pack, 12)
		make_supplier_price(self.item_code, self.supplier, price=1, uom=large_pack)
		material_request = self.make_material_request(1)

		created = create_purchase_orders_from_report([self.report_row(1, supplier_uom=large_pack)])

		purchase_order = frappe.get_doc("Purchase Order", created["purchase_orders"][0])
		self.assertEqual(flt(purchase_order.items[0].qty), 1)
		self.assertEqual(flt(purchase_order.items[0].stock_qty), 12)

		material_request.reload()
		self.assertEqual(flt(material_request.items[0].ordered_qty), 12)
