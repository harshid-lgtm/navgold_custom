# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, nowdate

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import make_item
from navgold_custom.navgold_custom.custom_script.material_request.material_request import (
	OPEN_STATUSES,
	reconcile_purchase_demand,
)
from navgold_custom.navgold_custom.custom_script.material_request.test_retire_dead_demand import (
	make_test_customer,
)
from navgold_custom.navgold_custom.custom_script.stock_entry.test_fba_clearing import (
	COMPANY,
	create_test_warehouse,
)
from navgold_custom.navgold_custom.custom_script.supplier.supplier import DAYS_OF_WEEK
from navgold_custom.navgold_custom.erpnext_compat import make_purchase_order, make_purchase_receipt
from navgold_custom.navgold_custom.report.navgold_forecast_report.test_navgold_forecast_report import (
	add_uom_conversion,
)


class TestReconcilePurchaseDemand(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.warehouse = create_test_warehouse("Test Reconcile Demand")
		cls.customer = make_test_customer()

	def setUp(self):
		super().setUp()
		self.item_code = make_item(f"TEST-RECONCILE-{self._testMethodName}", is_stock_item=1)

	def make_sales_order(self, qty, delivery_date=None, item_code=None, uom="Nos"):
		sales_order = frappe.new_doc("Sales Order")
		sales_order.customer = self.customer
		sales_order.company = COMPANY
		sales_order.fulfillment_channel = "MFN"
		sales_order.transaction_date = nowdate()
		sales_order.delivery_date = delivery_date or add_days(nowdate(), 3)
		sales_order.append(
			"items",
			{
				"item_code": item_code or self.item_code,
				"qty": qty,
				"uom": uom,
				"rate": 10,
				"warehouse": self.warehouse,
				"delivery_date": delivery_date or add_days(nowdate(), 3),
			},
		)
		sales_order.insert()
		sales_order.submit()
		return sales_order

	def auto_request_for(self, sales_order):
		# submitting the order already raises its request against an empty shelf. Building a second
		# one by hand puts the line over its own qty, which only passes where Stock Settings carries
		# an over-receipt allowance.
		names = set(
			frappe.get_all(
				"Material Request Item",
				filters={"sales_order_item": sales_order.items[0].name, "docstatus": 1},
				pluck="parent",
			)
		)
		self.assertEqual(len(names), 1)
		return frappe.get_doc("Material Request", names.pop())

	def cancel_auto_requests(self, *sales_orders):
		for sales_order in sales_orders:
			self.auto_request_for(sales_order).cancel()

	def receive_stock(self, qty, warehouse=None, item_code=None):
		receipt = frappe.new_doc("Stock Entry")
		receipt.stock_entry_type = "Material Receipt"
		receipt.company = COMPANY
		receipt.append(
			"items",
			{
				"item_code": item_code or self.item_code,
				"qty": qty,
				"t_warehouse": warehouse or self.warehouse,
				"basic_rate": 5,
			},
		)
		receipt.insert()
		receipt.submit()
		return receipt

	def issue_stock(self, qty):
		issue = frappe.new_doc("Stock Entry")
		issue.stock_entry_type = "Material Issue"
		issue.company = COMPANY
		issue.append(
			"items",
			{"item_code": self.item_code, "qty": qty, "s_warehouse": self.warehouse, "basic_rate": 5},
		)
		issue.insert()
		issue.submit()
		return issue

	def status_of(self, material_request):
		return frappe.db.get_value("Material Request", material_request.name, "status")

	def requested_qty_of(self, sales_order):
		return flt(frappe.db.get_value("Sales Order Item", sales_order.items[0].name, "requested_qty"))

	def open_material_requests_for(self, sales_order):
		return frappe.get_all(
			"Material Request Item",
			filters={"sales_order": sales_order.name, "docstatus": 1},
			fields=["parent"],
			pluck="parent",
		)

	def test_a_pack_bought_whole_retires_the_requests_it_now_covers(self):
		first_order = self.make_sales_order(qty=3)
		second_order = self.make_sales_order(qty=3)
		first_request = self.auto_request_for(first_order)
		second_request = self.auto_request_for(second_order)

		self.receive_stock(10)
		reconcile_purchase_demand()

		self.assertEqual(self.status_of(first_request), "Stopped")
		self.assertEqual(self.status_of(second_request), "Stopped")

	def test_the_earliest_delivery_date_keeps_the_cover(self):
		urgent_order = self.make_sales_order(qty=6, delivery_date=add_days(nowdate(), 1))
		later_order = self.make_sales_order(qty=6, delivery_date=add_days(nowdate(), 5))

		self.receive_stock(6)
		reconcile_purchase_demand()

		self.assertEqual(self.open_requests_for(urgent_order), [])
		self.assertEqual([qty for _parent, qty in self.open_requests_for(later_order)], [6.0])

	def test_stock_lost_to_another_order_brings_the_request_back(self):
		sales_order = self.make_sales_order(qty=3)
		material_request = self.auto_request_for(sales_order)

		self.receive_stock(3)
		reconcile_purchase_demand()
		self.assertEqual(self.status_of(material_request), "Stopped")
		self.assertEqual(self.requested_qty_of(sales_order), 0)

		self.issue_stock(3)
		reconcile_purchase_demand()

		revived = [
			name
			for name in self.open_material_requests_for(sales_order)
			if frappe.db.get_value("Material Request", name, "status") == "Pending"
		]
		self.assertEqual(len(revived), 1)
		self.assertNotIn(material_request.name, revived)

	def test_an_order_being_picked_is_left_alone(self):
		sales_order = self.make_sales_order(qty=3)
		material_request = self.auto_request_for(sales_order)
		self.receive_stock(10)
		self.make_pick_list(sales_order)

		reconcile_purchase_demand()

		self.assertEqual(self.status_of(material_request), "Pending")

	def test_a_request_spanning_an_order_being_picked_is_kept(self):
		covered_order = self.make_sales_order(qty=3)
		picked_order = self.make_sales_order(qty=3)
		# a request spanning two orders is never raised automatically, so the per-order ones have to
		# give their qty back before this one can claim the same lines
		self.cancel_auto_requests(covered_order, picked_order)
		material_request = frappe.new_doc("Material Request")
		material_request.company = COMPANY
		material_request.material_request_type = "Purchase"
		material_request.transaction_date = nowdate()
		material_request.schedule_date = add_days(nowdate(), 3)
		for order in (covered_order, picked_order):
			material_request.append(
				"items",
				{
					"item_code": self.item_code,
					"qty": 3,
					"uom": "Nos",
					"warehouse": self.warehouse,
					"schedule_date": add_days(nowdate(), 3),
					"sales_order": order.name,
					"sales_order_item": order.items[0].name,
				},
			)
		material_request.insert()
		material_request.submit()

		self.receive_stock(20)
		self.make_pick_list(picked_order)
		reconcile_purchase_demand()

		self.assertEqual(self.status_of(material_request), "Pending")

	def test_a_request_already_on_a_purchase_order_is_kept(self):
		sales_order = self.make_sales_order(qty=3)
		material_request = self.auto_request_for(sales_order)
		self.make_purchase_order(material_request)

		self.receive_stock(10)
		reconcile_purchase_demand()

		self.assertEqual(self.status_of(material_request), "Partially Ordered")

	def test_stock_staged_for_delivery_still_counts_as_cover(self):
		consolidation_warehouse = create_test_warehouse("Test Reconcile Consolidation")
		frappe.db.set_value("Warehouse", consolidation_warehouse, "is_rejected_warehouse", 1)

		with self.change_settings(
			"Navgold Settings", {"delivery_consolidation_warehouse": consolidation_warehouse}
		):
			sales_order = self.make_sales_order(qty=3)
			material_request = self.auto_request_for(sales_order)
			self.receive_stock(3, warehouse=consolidation_warehouse)

			reconcile_purchase_demand()

		self.assertEqual(self.status_of(material_request), "Stopped")

	def open_requests_where(self, condition):
		material_request = frappe.qb.DocType("Material Request")
		material_request_item = frappe.qb.DocType("Material Request Item")

		rows = (
			frappe.qb.from_(material_request_item)
			.join(material_request)
			.on(material_request_item.parent == material_request.name)
			.select(material_request_item.parent, material_request_item.stock_qty)
			.where(
				condition & (material_request.docstatus == 1) & material_request.status.isin(OPEN_STATUSES)
			)
		).run(as_dict=True)

		return sorted((row.parent, flt(row.stock_qty)) for row in rows)

	def open_requests_for_item(self):
		return self.open_requests_where(
			frappe.qb.DocType("Material Request Item").item_code == self.item_code
		)

	def open_requests_for(self, sales_order):
		return self.open_requests_where(
			frappe.qb.DocType("Material Request Item").sales_order_item == sales_order.items[0].name
		)

	def touching_this_item(self, material_requests):
		if not material_requests:
			return []
		return sorted(
			set(
				frappe.get_all(
					"Material Request Item",
					filters={"parent": ("in", material_requests), "item_code": self.item_code},
					pluck="parent",
				)
			)
		)

	def test_a_request_is_cut_to_the_qty_the_shelf_cannot_cover(self):
		sales_order = self.make_sales_order(qty=24)
		self.assertEqual([qty for _parent, qty in self.open_requests_for_item()], [24.0])

		self.receive_stock(6)
		reconcile_purchase_demand()

		open_requests = self.open_requests_for_item()
		self.assertEqual(len(open_requests), 1)
		self.assertEqual(open_requests[0][1], 18.0)
		self.assertEqual(self.requested_qty_of(sales_order), 18.0)

	def test_a_second_pass_over_an_unchanged_shelf_does_nothing(self):
		first_order = self.make_sales_order(qty=10, delivery_date=add_days(nowdate(), 1))
		second_order = self.make_sales_order(qty=10, delivery_date=add_days(nowdate(), 5))

		self.receive_stock(10)
		reconcile_purchase_demand()

		settled_requests = self.open_requests_for_item()
		settled_requested_qty = (
			self.requested_qty_of(first_order),
			self.requested_qty_of(second_order),
		)

		second_pass = reconcile_purchase_demand()

		self.assertEqual(self.touching_this_item(second_pass["retired"]), [])
		self.assertEqual(self.touching_this_item(second_pass["requested"]), [])
		self.assertEqual(self.open_requests_for_item(), settled_requests)
		self.assertEqual(
			(self.requested_qty_of(first_order), self.requested_qty_of(second_order)),
			settled_requested_qty,
		)

	def make_pick_list(self, sales_order):
		pick_list = frappe.new_doc("Pick List")
		pick_list.company = COMPANY
		pick_list.purpose = "Delivery"
		pick_list.append(
			"locations",
			{
				"item_code": sales_order.items[0].item_code,
				"qty": sales_order.items[0].qty,
				"stock_qty": sales_order.items[0].stock_qty,
				"uom": sales_order.items[0].uom,
				"conversion_factor": sales_order.items[0].conversion_factor,
				"warehouse": self.warehouse,
				"sales_order": sales_order.name,
				"sales_order_item": sales_order.items[0].name,
			},
		)
		pick_list.insert()
		return pick_list

	def make_purchase_order(self, material_request):
		supplier = make_test_supplier()
		purchase_order = frappe.new_doc("Purchase Order")
		purchase_order.supplier = supplier
		purchase_order.company = COMPANY
		purchase_order.transaction_date = nowdate()
		purchase_order.schedule_date = add_days(nowdate(), 3)
		purchase_order.append(
			"items",
			{
				"item_code": material_request.items[0].item_code,
				"qty": 1,
				"uom": "Nos",
				"conversion_factor": 1,
				"rate": 5,
				"schedule_date": add_days(nowdate(), 3),
				"warehouse": self.warehouse,
				"material_request": material_request.name,
				"material_request_item": material_request.items[0].name,
			},
		)
		purchase_order.insert()
		purchase_order.submit()
		return purchase_order

	def order_in_full(self, material_request):
		purchase_order = make_purchase_order(material_request.name)
		purchase_order.supplier = make_test_supplier()
		purchase_order.schedule_date = add_days(nowdate(), 3)
		for row in purchase_order.items:
			row.rate = 5
			row.warehouse = self.warehouse
			# To Purchase writes its lines without the order link, so receiving them reserves nothing
			row.sales_order = None
			row.sales_order_item = None
		purchase_order.insert()
		purchase_order.submit()
		return purchase_order

	def receive_in_full(self, purchase_order):
		purchase_receipt = make_purchase_receipt(purchase_order.name)
		purchase_receipt.insert()
		purchase_receipt.submit()
		return purchase_receipt

	def requested_for(self, sales_order):
		return sum(
			flt(qty)
			for qty in frappe.get_all(
				"Material Request Item",
				filters={"sales_order_item": sales_order.items[0].name, "docstatus": 1},
				pluck="stock_qty",
			)
		)

	def test_stock_received_and_then_lost_is_asked_for_again(self):
		sales_order = self.make_sales_order(qty=3)
		material_request = self.auto_request_for(sales_order)
		self.receive_in_full(self.order_in_full(material_request))
		self.assertEqual(self.status_of(material_request), "Received")

		# the goods went out on another order, or a stock count took them away
		self.issue_stock(3)
		result = reconcile_purchase_demand()

		self.assertNotIn(sales_order.name, result["blocked"])
		self.assertEqual([qty for _parent, qty in self.open_requests_for(sales_order)], [3.0])

	def test_a_request_already_on_a_purchase_order_is_not_asked_for_twice(self):
		sales_order = self.make_sales_order(qty=3)
		self.order_in_full(self.auto_request_for(sales_order))

		result = reconcile_purchase_demand()

		self.assertNotIn(sales_order.name, result["blocked"])
		self.assertEqual(self.requested_for(sales_order), 3)

	def test_a_closed_purchase_order_no_longer_counts_as_cover(self):
		sales_order = self.make_sales_order(qty=3)
		purchase_order = self.order_in_full(self.auto_request_for(sales_order))
		purchase_order.update_status("Closed")

		reconcile_purchase_demand()

		self.assertEqual([qty for _parent, qty in self.open_requests_for(sales_order)], [3.0])

	def test_a_pack_line_that_lost_its_shelf_cover_is_topped_up(self):
		add_uom_conversion(self.item_code, "Pack of 6", 6)
		self.receive_stock(4)
		sales_order = self.make_sales_order(qty=1, uom="Pack of 6")
		self.assertEqual(self.requested_for(sales_order), 2, "the shelf covered 4 of the pack")

		self.issue_stock(4)
		result = reconcile_purchase_demand()

		self.assertNotIn(sales_order.name, result["blocked"])
		self.assertEqual(self.requested_for(sales_order), 6)


def make_test_supplier():
	name = "Test Reconcile Demand Supplier"
	if frappe.db.exists("Supplier", name):
		return name
	supplier = frappe.new_doc("Supplier")
	supplier.supplier_name = name
	supplier.supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
	supplier.custom_rank = 1
	for day in DAYS_OF_WEEK:
		supplier.append("custom_lead_time", {"day": day, "lead_time_before_12": 1, "lead_time_after_12": 2})
	supplier.insert()
	return supplier.name
