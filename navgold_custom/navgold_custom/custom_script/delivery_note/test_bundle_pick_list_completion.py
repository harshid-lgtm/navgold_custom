# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import make_item
from navgold_custom.navgold_custom.custom_script.pick_list.test_delivery_consolidation import (
	COMPANY,
	create_bundle_parent_item,
	create_test_customer,
	create_test_warehouse,
	receive_stock,
)
from navgold_custom.patches.v1.complete_pick_lists_stranded_by_bundle_rows import (
	execute as complete_stranded_pick_lists,
)


class TestBundlePickListCompletion(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.warehouse = create_test_warehouse("Test Bundle Completion")
		create_test_customer()

	def setUp(self):
		super().setUp()
		tag = frappe.generate_hash(length=6)
		self.component_one = make_item(f"TEST-BPC-{tag}-A", is_stock_item=1)
		self.component_two = make_item(f"TEST-BPC-{tag}-B", is_stock_item=1)
		for item_code in (self.component_one, self.component_two):
			receive_stock(item_code, self.warehouse, 20)

		# two distinct components on purpose: the bug only shows when the parent Delivery Note row
		# can credit one of them and leave the other at zero
		self.bundle_parent = create_bundle_parent_item(f"TEST-BPC-{tag}-BUNDLE")
		bundle = frappe.new_doc("Product Bundle")
		bundle.new_item_code = self.bundle_parent
		bundle.append("items", {"item_code": self.component_one, "qty": 2})
		bundle.append("items", {"item_code": self.component_two, "qty": 1})
		bundle.insert()
		if bundle.meta.is_submittable:
			bundle.submit()

	def make_pick_list_for(self, *sales_orders, tote=None):
		pick_list = frappe.new_doc("Pick List")
		pick_list.company = COMPANY
		pick_list.purpose = "Delivery"
		pick_list.pick_manually = 1
		if tote:
			pick_list.custom_tote = tote
		for sales_order in sales_orders:
			for packed in sales_order.packed_items:
				pick_list.append(
					"locations",
					{
						"item_code": packed.item_code,
						"warehouse": self.warehouse,
						"qty": packed.qty,
						"stock_qty": packed.qty,
						"picked_qty": packed.qty,
						"uom": "Nos",
						"stock_uom": "Nos",
						"conversion_factor": 1,
						"sales_order": sales_order.name,
						"sales_order_item": sales_order.items[0].name,
						"product_bundle_item": packed.name,
					},
				)
		pick_list.insert()
		pick_list.submit()
		return pick_list

	def make_bundle_order(self):
		"""Pin the warehouse on the order: erpnext rebuilds packed-item warehouses from the Sales
		Order on every Delivery Note validate, so setting them on the note itself does not hold."""
		sales_order = frappe.new_doc("Sales Order")
		sales_order.customer = create_test_customer()
		sales_order.company = COMPANY
		sales_order.delivery_date = nowdate()
		sales_order.append(
			"items",
			{
				"item_code": self.bundle_parent,
				"qty": 1,
				"uom": "Nos",
				"conversion_factor": 1,
				"rate": 100,
				"warehouse": self.warehouse,
			},
		)
		sales_order.save()
		for packed in sales_order.packed_items:
			frappe.db.set_value("Packed Item", packed.name, "warehouse", self.warehouse)
		sales_order.submit()
		sales_order.reload()
		return sales_order

	def deliver(self, sales_order):
		from erpnext.selling.doctype.sales_order.mapper import make_delivery_note

		delivery_note = frappe.get_doc(make_delivery_note(sales_order.name))
		for row in delivery_note.items:
			row.warehouse = self.warehouse
		for row in delivery_note.get("packed_items") or []:
			row.warehouse = self.warehouse
		delivery_note.insert()
		delivery_note.submit()
		return delivery_note

	def test_a_bundle_order_completes_its_pick_list(self):
		"""The reported bug: every component ships, one row gets credited, the rest stay at zero."""
		sales_order = self.make_bundle_order()
		pick_list = self.make_pick_list_for(sales_order)

		self.deliver(sales_order)

		pick_list.reload()
		self.assertEqual(pick_list.status, "Completed")

	def test_a_pick_list_still_owed_is_left_alone(self):
		"""A second order on the same pick list that has not shipped must keep it open."""
		delivered_order = self.make_bundle_order()
		pending_order = self.make_bundle_order()
		pick_list = self.make_pick_list_for(delivered_order, pending_order)

		self.deliver(delivered_order)

		pick_list.reload()
		self.assertNotEqual(pick_list.status, "Completed")

	def test_cancelling_the_delivery_reopens_the_pick_list(self):
		sales_order = self.make_bundle_order()
		pick_list = self.make_pick_list_for(sales_order)
		delivery_note = self.deliver(sales_order)
		pick_list.reload()
		self.assertEqual(pick_list.status, "Completed")

		delivery_note.cancel()

		pick_list.reload()
		self.assertNotEqual(pick_list.status, "Completed")

	def test_the_backfill_completes_a_stranded_pick_list(self):
		sales_order = self.make_bundle_order()
		pick_list = self.make_pick_list_for(sales_order, tote="T-TEST-01")
		self.deliver(sales_order)

		# rewind to how every bundle pick list looked before the fix
		frappe.db.set_value(
			"Pick List",
			pick_list.name,
			{"status": "Partly Delivered", "per_delivered": 50, "delivery_status": "Partly Delivered"},
		)

		complete_stranded_pick_lists()

		pick_list.reload()
		self.assertEqual(pick_list.status, "Completed")

	def test_a_plain_pick_list_is_left_to_erpnext(self):
		"""No bundle, no consolidation: ERPNext tracks it row by row and must stay the authority."""
		sales_order = frappe.new_doc("Sales Order")
		sales_order.customer = create_test_customer()
		sales_order.company = COMPANY
		sales_order.delivery_date = nowdate()
		sales_order.append(
			"items",
			{
				"item_code": self.component_one,
				"qty": 1,
				"uom": "Nos",
				"conversion_factor": 1,
				"rate": 100,
				"warehouse": self.warehouse,
			},
		)
		sales_order.save()
		sales_order.submit()

		pick_list = frappe.new_doc("Pick List")
		pick_list.company = COMPANY
		pick_list.purpose = "Delivery"
		pick_list.pick_manually = 1
		pick_list.append(
			"locations",
			{
				"item_code": self.component_one,
				"warehouse": self.warehouse,
				"qty": 1,
				"stock_qty": 1,
				"picked_qty": 1,
				"uom": "Nos",
				"stock_uom": "Nos",
				"conversion_factor": 1,
				"sales_order": sales_order.name,
				"sales_order_item": sales_order.items[0].name,
			},
		)
		pick_list.insert()
		pick_list.submit()

		self.deliver(sales_order)

		pick_list.reload()
		self.assertNotEqual(
			pick_list.status, "Completed", "a plain pick list must be left to erpnext's own tracking"
		)

	def test_the_backfill_leaves_an_undelivered_pick_list_alone(self):
		sales_order = self.make_bundle_order()
		pick_list = self.make_pick_list_for(sales_order, tote="T-TEST-02")

		complete_stranded_pick_lists()

		pick_list.reload()
		self.assertNotEqual(pick_list.status, "Completed")
