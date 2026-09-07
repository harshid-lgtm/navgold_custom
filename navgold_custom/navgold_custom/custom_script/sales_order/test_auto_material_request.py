# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

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
	use_afn_warehouse,
)
from navgold_custom.navgold_custom.erpnext_compat import make_purchase_order
from navgold_custom.navgold_custom.report.navgold_forecast_report.test_navgold_forecast_report import (
	add_uom_conversion,
	make_supplier,
)
from navgold_custom.patches.v1.request_stock_for_orders_left_uncovered import (
	execute as execute_uncovered_patch,
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

	def request_for(self, sales_order):
		names = frappe.get_all(
			"Material Request Item",
			filters={"sales_order": sales_order.name, "docstatus": 1},
			pluck="parent",
			distinct=True,
		)
		return frappe.get_doc("Material Request", names[0]) if names else None

	def swallowed_failure(self, sales_order):
		"""create_material_request catches everything and only logs, so a missing request looks
		identical to no shortage. Surface the traceback it hid or the assertion says nothing."""
		rows = frappe.get_all(
			"Error Log",
			filters={"method": f"Auto MR Creation Failed - SO {sales_order.name}"},
			fields=["error"],
			limit=1,
		)
		return rows[0].error[-2000:] if rows else "no Error Log entry — there was simply no shortage"

	def test_request_is_capped_at_its_own_sales_order_line(self):
		self.strand_demand(20)

		sales_order = self.make_sales_order([(5, "Nos")])

		material_request = self.request_for(sales_order)
		self.assertIsNotNone(material_request, self.swallowed_failure(sales_order))
		self.assertEqual(flt(material_request.items[0].stock_qty), 5)

	def test_two_lines_of_one_item_split_the_shortage(self):
		self.strand_demand(20)

		sales_order = self.make_sales_order([(4, "Nos"), (3, "Nos")])

		material_request = self.request_for(sales_order)
		self.assertIsNotNone(material_request, self.swallowed_failure(sales_order))
		self.assertEqual(
			sorted(flt(row.stock_qty) for row in material_request.items),
			[3.0, 4.0],
		)

	def test_a_pack_line_is_requested_in_its_own_uom(self):
		add_uom_conversion(self.item_code, "Pack of 6", 6)
		# Stand the shortage up before this order rather than leaning on its own reservation, which
		# lands at a different point in on_submit across ERPNext versions. The subject here is the
		# pack conversion, not when the Bin catches up.
		self.strand_demand(12)

		sales_order = self.make_sales_order([(2, "Pack of 6")])
		self.assertEqual(flt(sales_order.items[0].stock_qty), 12)

		material_request = self.request_for(sales_order)
		self.assertIsNotNone(material_request, self.swallowed_failure(sales_order))
		requested = material_request.items[0]
		self.assertEqual(requested.uom, "Pack of 6")
		self.assertEqual(flt(requested.conversion_factor), 6)
		self.assertEqual(flt(requested.qty), 2)
		self.assertEqual(flt(requested.stock_qty), 12)

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

	def test_a_failed_request_alerts_instead_of_going_quiet(self):
		self.strand_demand(20)

		with (
			patch(
				"navgold_custom.navgold_custom.custom_script.sales_order.sales_order.make_material_request",
				side_effect=frappe.ValidationError("upstream said no"),
			),
			patch(
				"navgold_custom.navgold_custom.custom_script.sales_order.sales_order.alert_material_request_failed"
			) as alert,
		):
			sales_order = self.make_sales_order([(5, "Nos")])

		self.assertIsNone(self.request_for(sales_order), "the request was meant to fail")
		self.assertEqual(sales_order.docstatus, 1, "the order must survive its request failing")
		alert.assert_called_once()
		self.assertEqual(alert.call_args.args[0], sales_order.name)


class TestUnclaimedSupplyNeverCoversAnOrder(IntegrationTestCase):
	"""The pack-rounding trap: an order for 3 is bought as a pack of 10, and the next order for 3
	used to read that surplus as its own cover. Nothing recorded the claim, so retiring the request
	stranded the second order silently. Supply nobody has claimed no longer counts for anybody."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.warehouse = create_test_warehouse("Test Unclaimed Supply")
		cls.customer = make_test_customer()
		cls.afn_warehouse = create_test_warehouse("Test Unclaimed Supply AFN")
		# Production marks the AFN warehouse rejected and transfers FBA qty into it, which is what
		# keeps Amazon-held stock out of every shelf reading. Mirror that, or the test proves nothing.
		frappe.db.set_value("Warehouse", cls.afn_warehouse, "is_rejected_warehouse", 1)
		use_afn_warehouse(cls, cls.afn_warehouse)

	def setUp(self):
		super().setUp()
		self.item_code = make_item(f"TEST-UNCLAIMED-{self._testMethodName}", is_stock_item=1)

	def order_for(self, qty, fulfillment_channel="MFN"):
		sales_order = frappe.new_doc("Sales Order")
		sales_order.customer = self.customer
		sales_order.company = COMPANY
		sales_order.fulfillment_channel = fulfillment_channel
		sales_order.transaction_date = nowdate()
		sales_order.delivery_date = add_days(nowdate(), 1)
		sales_order.append(
			"items",
			{
				"item_code": self.item_code,
				"qty": qty,
				"uom": "Nos",
				"rate": 10,
				"warehouse": self.warehouse,
				"delivery_date": add_days(nowdate(), 1),
			},
		)
		sales_order.insert()
		sales_order.submit()
		return sales_order

	def requested_qty_for(self, sales_order):
		rows = frappe.get_all(
			"Material Request Item",
			filters={"sales_order": sales_order.name, "docstatus": 1},
			fields=["stock_qty"],
		)
		return sum(flt(row.stock_qty) for row in rows)

	def receive_into(self, warehouse, qty):
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.stock_entry_type = "Material Receipt"
		stock_entry.company = COMPANY
		stock_entry.append(
			"items",
			{"item_code": self.item_code, "qty": qty, "t_warehouse": warehouse, "basic_rate": 5},
		)
		stock_entry.insert()
		stock_entry.submit()
		return stock_entry

	def buy_a_whole_pack(self, qty):
		"""A submitted Material Request for the full pack, the way purchasing rounds a shortage of 3
		up to the supplier's pack of 10. Raises Bin.indented_qty, which projected_qty counted."""
		material_request = frappe.new_doc("Material Request")
		material_request.material_request_type = "Purchase"
		material_request.company = COMPANY
		material_request.schedule_date = add_days(nowdate(), 3)
		material_request.append(
			"items",
			{
				"item_code": self.item_code,
				"qty": qty,
				"uom": "Nos",
				"warehouse": self.warehouse,
				"schedule_date": add_days(nowdate(), 3),
			},
		)
		material_request.insert()
		material_request.submit()
		return material_request

	def test_a_pack_bought_for_an_earlier_order_does_not_cover_the_next_one(self):
		first_order = self.order_for(3)
		self.assertEqual(self.requested_qty_for(first_order), 3, "the first order asks for its own 3")

		self.buy_a_whole_pack(10)

		second_order = self.order_for(3)
		self.assertEqual(
			self.requested_qty_for(second_order),
			3,
			"the pack of 10 belongs to the first order — the second must still raise its own request",
		)

	def order_the_pack_from_a_supplier(self, material_request):
		"""Turn the request into a submitted Purchase Order, moving the qty from Bin.indented_qty
		to Bin.ordered_qty — the other half of projected_qty this decision used to read."""
		purchase_order = make_purchase_order(material_request.name)
		purchase_order.supplier = make_supplier(f"NG Unclaimed {frappe.generate_hash(length=6)}")
		purchase_order.schedule_date = add_days(nowdate(), 3)
		for row in purchase_order.items:
			row.rate = 5
		purchase_order.insert()
		purchase_order.submit()
		return purchase_order

	def test_an_open_purchase_order_does_not_cover_the_next_order(self):
		material_request = self.buy_a_whole_pack(10)
		self.order_the_pack_from_a_supplier(material_request)

		sales_order = self.order_for(3)

		self.assertEqual(
			self.requested_qty_for(sales_order),
			3,
			"stock on order but not yet on the shelf cannot be shipped to this customer",
		)

	def test_stock_that_has_landed_does_cover_the_next_order(self):
		"""The other half of the trade: over-buying is self-correcting because the surplus arrives
		as real stock, and a real shelf is exactly what the next order is allowed to read."""
		self.receive_into(self.warehouse, 10)

		sales_order = self.order_for(3)

		self.assertEqual(self.requested_qty_for(sales_order), 0)

	def test_stock_held_at_amazon_does_not_cover_a_merchant_fulfilled_order(self):
		"""Not a behaviour change — a guard on the config that carries it. AFN stock is kept out of
		our shelf purely by the AFN warehouse being flagged rejected, so unflagging it would quietly
		start covering merchant-fulfilled orders with stock only Amazon can ship."""
		self.receive_into(self.afn_warehouse, 10)

		sales_order = self.order_for(3)

		self.assertEqual(
			self.requested_qty_for(sales_order),
			3,
			"AFN stock sits on Amazon's shelf — we cannot pick it for a merchant-fulfilled order",
		)


class TestRequestStockForOrdersLeftUncovered(IntegrationTestCase):
	"""The backlog half of the fix: orders already stranded by the old reading get their requests
	raised once, and only once."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.warehouse = create_test_warehouse("Test Uncovered Backfill")
		cls.customer = make_test_customer()

	def setUp(self):
		super().setUp()
		self.item_code = make_item(f"TEST-BACKFILL-{self._testMethodName}", is_stock_item=1)

	def strand_an_order(self, qty, amazon_order_status="Unshipped"):
		"""An order submitted with no request of its own — what the old reading left behind whenever
		it judged the order covered by someone else's supply."""
		sales_order = frappe.new_doc("Sales Order")
		sales_order.customer = self.customer
		sales_order.company = COMPANY
		sales_order.fulfillment_channel = "MFN"
		sales_order.amazon_order_status = amazon_order_status
		sales_order.transaction_date = nowdate()
		sales_order.delivery_date = add_days(nowdate(), 1)
		sales_order.append(
			"items",
			{
				"item_code": self.item_code,
				"qty": qty,
				"uom": "Nos",
				"rate": 10,
				"warehouse": self.warehouse,
				"delivery_date": add_days(nowdate(), 1),
			},
		)
		sales_order.insert()
		# submit without the hook, so the order lands stranded exactly as the old reading left it
		sales_order.flags.ignore_validate_update_after_submit = True
		with patch(
			"navgold_custom.navgold_custom.custom_script.sales_order.sales_order.create_material_request"
		):
			sales_order.submit()
		return sales_order

	def requested_qty_for(self, sales_order):
		rows = frappe.get_all(
			"Material Request Item",
			filters={"sales_order": sales_order.name, "docstatus": 1},
			fields=["stock_qty"],
		)
		return sum(flt(row.stock_qty) for row in rows)

	def test_a_stranded_order_gets_its_request_raised(self):
		sales_order = self.strand_an_order(4)
		self.assertEqual(self.requested_qty_for(sales_order), 0, "the order must start stranded")

		execute_uncovered_patch()

		self.assertEqual(self.requested_qty_for(sales_order), 4)

	def test_running_it_twice_does_not_request_the_stock_twice(self):
		sales_order = self.strand_an_order(4)

		execute_uncovered_patch()
		execute_uncovered_patch()

		self.assertEqual(
			self.requested_qty_for(sales_order),
			4,
			"requested_qty caps the line, so a re-run on migrate must be a no-op",
		)

	def move_stock(self, qty, receive=True):
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.stock_entry_type = "Material Receipt" if receive else "Material Issue"
		stock_entry.company = COMPANY
		row = {"item_code": self.item_code, "qty": qty, "basic_rate": 5}
		row["t_warehouse" if receive else "s_warehouse"] = self.warehouse
		stock_entry.append("items", row)
		stock_entry.insert()
		stock_entry.submit()

	def order_through_the_hook(self, qty):
		sales_order = frappe.new_doc("Sales Order")
		sales_order.customer = self.customer
		sales_order.company = COMPANY
		sales_order.fulfillment_channel = "MFN"
		sales_order.amazon_order_status = "Unshipped"
		sales_order.transaction_date = nowdate()
		sales_order.delivery_date = add_days(nowdate(), 1)
		sales_order.append(
			"items",
			{
				"item_code": self.item_code,
				"qty": qty,
				"uom": "Nos",
				"rate": 10,
				"warehouse": self.warehouse,
				"delivery_date": add_days(nowdate(), 1),
			},
		)
		sales_order.insert()
		sales_order.submit()
		return sales_order

	def requests_for(self, sales_order):
		return frappe.get_all(
			"Material Request Item",
			filters={"sales_order": sales_order.name, "docstatus": 1},
			pluck="parent",
			distinct=True,
		)

	def test_an_order_left_half_covered_is_topped_up_to_its_own_line(self):
		"""SAL-ORD-2026-29870's shape: it wanted 2, the shelf held 1, so it asked for 1 — and then
		another order took the unit it had been sized against. Its request is never re-sized, so
		without this sweep it stays short by exactly the stock it lost."""
		self.move_stock(1)
		sales_order = self.order_through_the_hook(2)
		self.assertEqual(self.requested_qty_for(sales_order), 1, "the shelf unit covered half the line")

		self.move_stock(1, receive=False)

		execute_uncovered_patch()

		self.assertEqual(
			self.requested_qty_for(sales_order),
			2,
			"the unit it was counting on is gone, so the request must cover the whole line",
		)
		# The first request is submitted and cannot be edited, so the shortfall arrives as a second
		# one. Purchasing reads the pair as one figure: To Purchase sums pending qty per item.
		self.assertEqual(len(self.requests_for(sales_order)), 2)

	def test_an_order_amazon_has_already_shipped_is_left_alone(self):
		sales_order = self.strand_an_order(4, amazon_order_status="Shipped")

		execute_uncovered_patch()

		self.assertEqual(
			self.requested_qty_for(sales_order),
			0,
			"the goods have already left — buying replacements is not a fix",
		)
