# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, nowdate

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import (
	make_customer,
	make_item,
	receive_stock,
)
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
	get_data,
)
from navgold_custom.navgold_custom.utils import get_free_qty_map

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

	def make_material_request(self, qty, is_amazon_pending=0, days_out=1):
		material_request = frappe.new_doc("Material Request")
		material_request.company = COMPANY
		material_request.material_request_type = "Purchase"
		material_request.transaction_date = nowdate()
		material_request.schedule_date = add_days(nowdate(), days_out)
		material_request.custom_is_amazon_pending = is_amazon_pending
		material_request.append(
			"items",
			{
				"item_code": self.item_code,
				"qty": qty,
				"uom": "Nos",
				"conversion_factor": 1,
				"warehouse": self.warehouse,
				"schedule_date": add_days(nowdate(), days_out),
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

	def ordered_by_request(self, purchase_order):
		return {
			item.material_request: flt(item.qty) for item in purchase_order.items if item.material_request
		}

	def unrequested_lines(self, purchase_order):
		return [flt(item.qty) for item in purchase_order.items if not item.material_request]

	def test_a_purchase_order_rounds_demand_up_to_a_whole_supplier_pack(self):
		# 92 is not a multiple of 6, so the order has to reach for a 16th pack. The request is still
		# booked for the 92 it asked for; the 4 that fill the pack sit on a line of their own.
		material_request = self.make_material_request(92)

		created = create_purchase_orders_from_report([self.report_row(92)])

		self.assertEqual(len(created["purchase_orders"]), 1)

		purchase_order = frappe.get_doc("Purchase Order", created["purchase_orders"][0])
		self.assertEqual(purchase_order.docstatus, 1)
		self.assertEqual(flt(purchase_order.items[0].qty), 92)
		self.assertEqual(purchase_order.items[0].material_request, material_request.name)
		self.assertEqual(flt(purchase_order.items[1].qty), 4)
		self.assertIsNone(purchase_order.items[1].material_request)
		self.assertEqual(sum(flt(item.qty) for item in purchase_order.items), 96)

		material_request.reload()
		self.assertEqual(flt(material_request.items[0].ordered_qty), 92)

	def test_pack_aligned_demand_orders_exactly_what_was_asked_for(self):
		self.make_material_request(90)

		created = create_purchase_orders_from_report([self.report_row(90)])

		purchase_order = frappe.get_doc("Purchase Order", created["purchase_orders"][0])
		self.assertEqual(flt(purchase_order.items[0].qty), 90)
		self.assertEqual(flt(purchase_order.items[0].stock_qty), 90)

	def test_a_pack_supplier_is_ordered_in_single_units(self):
		# Suppliers short-ship loose units; a line held in packs then has to be booked in as a
		# fraction of a pack, so the order is written in the item's own unit from the start.
		self.make_material_request(10)

		created = create_purchase_orders_from_report([self.report_row(10)])

		purchase_order = frappe.get_doc("Purchase Order", created["purchase_orders"][0])
		for purchase_order_item in purchase_order.items:
			self.assertEqual(purchase_order_item.uom, "Nos")
			self.assertEqual(flt(purchase_order_item.conversion_factor), 1)

		self.assertEqual(sum(flt(item.qty) for item in purchase_order.items), 12)

	def test_the_per_unit_supplier_price_is_charged_on_every_unit(self):
		# Supplier price is per Nos; on a pack line it was billed once per pack, a sixth of the cost.
		self.make_material_request(18)
		row = self.report_row(18)
		row["unit_price"] = 0.69

		created = create_purchase_orders_from_report([row])

		purchase_order = frappe.get_doc("Purchase Order", created["purchase_orders"][0])
		self.assertEqual(flt(purchase_order.items[0].amount, 2), 12.42)

	def test_two_requests_share_one_pack_rounding(self):
		# PUR-ORD-2026-00051, item 429027: requests of 12 and 11 against a Pack of 9. Rounding each
		# request on its own gave 18 + 9 — the second was left 2 short and asked again later.
		pack_of_nine = "Pack of 9"
		add_uom_conversion(self.item_code, pack_of_nine, 9)
		make_supplier_price(self.item_code, self.supplier, price=1, uom=pack_of_nine)
		asked_by_request = {self.make_material_request(qty).name: qty for qty in (12, 11)}

		created = create_purchase_orders_from_report([self.report_row(23, supplier_uom=pack_of_nine)])

		purchase_order = frappe.get_doc("Purchase Order", created["purchase_orders"][0])
		self.assertEqual(self.ordered_by_request(purchase_order), asked_by_request)
		self.assertEqual(self.unrequested_lines(purchase_order), [4])
		self.assertEqual(sum(flt(item.qty) for item in purchase_order.items), 27)

	def test_many_small_requests_round_once_for_the_item(self):
		# PUR-ORD-2026-00052, item 433896: five requests for 24 units of a Pack of 5 bought 35 when
		# each was rounded on its own. One rounding for the item buys 25.
		pack_of_five = "Pack of 5"
		add_uom_conversion(self.item_code, pack_of_five, 5)
		make_supplier_price(self.item_code, self.supplier, price=1, uom=pack_of_five)
		asked_by_request = {self.make_material_request(qty).name: qty for qty in (6, 4, 4, 6, 4)}

		created = create_purchase_orders_from_report([self.report_row(24, supplier_uom=pack_of_five)])

		purchase_order = frappe.get_doc("Purchase Order", created["purchase_orders"][0])
		self.assertEqual(self.ordered_by_request(purchase_order), asked_by_request)
		self.assertEqual(self.unrequested_lines(purchase_order), [1])
		self.assertEqual(sum(flt(item.qty) for item in purchase_order.items), 25)

	def test_an_amazon_pending_request_is_left_alone_by_an_ordinary_order(self):
		# PUR-ORD-2026-00059, item 433896: demand was sized from the ordinary pool, but the order
		# filled the Amazon-pending requests first because they fell due earlier, and the demand
		# that paid for the stock stayed open.
		amazon_pending = self.make_material_request(6, is_amazon_pending=1, days_out=1)
		ordinary = self.make_material_request(4, days_out=2)

		created = create_purchase_orders_from_report([self.report_row(4)])

		purchase_order = frappe.get_doc("Purchase Order", created["purchase_orders"][0])
		self.assertEqual(list(self.ordered_by_request(purchase_order)), [ordinary.name])

		amazon_pending.reload()
		self.assertEqual(flt(amazon_pending.items[0].ordered_qty), 0)

	def test_an_amazon_pending_order_only_fills_amazon_pending_requests(self):
		amazon_pending = self.make_material_request(6, is_amazon_pending=1, days_out=2)
		ordinary = self.make_material_request(4, days_out=1)

		created = create_purchase_orders_from_report([self.report_row(6)], is_amazon_pending=1)

		purchase_order = frappe.get_doc("Purchase Order", created["purchase_orders"][0])
		self.assertEqual(list(self.ordered_by_request(purchase_order)), [amazon_pending.name])

		ordinary.reload()
		self.assertEqual(flt(ordinary.items[0].ordered_qty), 0)

	def test_supplier_stock_drops_by_the_units_ordered(self):
		self.make_material_request(92)

		create_purchase_orders_from_report([self.report_row(92)])

		remaining_stock = frappe.db.get_value(
			"Supplier Item Price and Stock",
			{"supplier": self.supplier, "item": self.item_code, "uom": PACK_UOM},
			"stock_qty",
		)
		self.assertEqual(remaining_stock, 100 - 96)

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
		self.assertEqual(self.unrequested_lines(purchase_order), [11])
		self.assertEqual(sum(flt(item.qty) for item in purchase_order.items), 12)

		material_request.reload()
		self.assertEqual(flt(material_request.items[0].ordered_qty), 1)


class TestToPurchaseDraftDemand(IntegrationTestCase):
	"""Purchasing is the one reader that nets off draft orders: those units are already sold, so
	buying against them would under-order. Every other reader asks what is physically sellable."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.warehouse = create_test_warehouse("Test To Purchase Drafts")
		cls.customer = make_customer(f"NG To Purchase Draft {frappe.generate_hash(length=6)}")

	def stocked_item(self, qty):
		item_code = make_item(f"TEST-DRAFT-DEMAND-{frappe.generate_hash(length=6)}", is_stock_item=1)
		receive_stock(item_code, qty, self.warehouse, COMPANY)
		return item_code

	def draft_order(self, item_code, qty, fulfillment_channel):
		sales_order = frappe.new_doc("Sales Order")
		sales_order.customer = self.customer
		sales_order.company = COMPANY
		sales_order.transaction_date = nowdate()
		sales_order.delivery_date = nowdate()
		sales_order.amazon_order_id = f"TEST-{frappe.generate_hash(length=8)}"
		sales_order.fulfillment_channel = fulfillment_channel
		sales_order.amazon_order_status = "Pending"
		sales_order.append(
			"items",
			{
				"item_code": item_code,
				"qty": qty,
				"rate": 10,
				"warehouse": self.warehouse,
				"delivery_date": nowdate(),
			},
		)
		sales_order.insert()

	def free_qty(self, item_code, deduct_draft_orders):
		return flt(
			get_free_qty_map([item_code], deduct_draft_orders=deduct_draft_orders)
			.get(item_code, {})
			.get("free_qty")
		)

	def test_purchasing_nets_off_an_mfn_draft_but_the_shelf_reading_does_not(self):
		item_code = self.stocked_item(10)
		self.draft_order(item_code, 4, "MFN")

		self.assertEqual(self.free_qty(item_code, deduct_draft_orders=True), 6, "4 are already sold")
		self.assertEqual(
			self.free_qty(item_code, deduct_draft_orders=False),
			10,
			"a draft reserves nothing, so the shelf still holds 10 to sell and to transfer",
		)

	def test_an_afn_draft_is_never_netted_off_even_for_purchasing(self):
		item_code = self.stocked_item(10)
		self.draft_order(item_code, 4, "AFN")

		self.assertEqual(
			self.free_qty(item_code, deduct_draft_orders=True),
			10,
			"an AFN order ships from Amazon's warehouse and never draws on this shelf",
		)


class TestToPurchaseSupplierChoice(IntegrationTestCase):
	"""Which supplier the report hands to the buyer: rank order, falling through to the next one
	when the top-ranked supplier cannot cover the demand."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.warehouse = create_test_warehouse("Test To Purchase Choice")

	def setUp(self):
		super().setUp()
		self.item_code = make_item(f"TEST-TO-PURCHASE-RANK-{frappe.generate_hash(length=6)}", is_stock_item=1)

	def offer(self, supplier_name, price, rank, stock_qty):
		supplier = make_supplier(f"{supplier_name} {frappe.generate_hash(length=6)}")
		name = make_supplier_price(self.item_code, supplier, price=price)
		frappe.db.set_value("Supplier Item Price and Stock", name, {"rank": rank, "stock_qty": stock_qty})
		return supplier

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

	def rows_for_item(self):
		rows, _max_offers, _reference_suppliers = get_data({})
		return [row for row in rows if row["item_code"] == self.item_code]

	def test_the_top_ranked_supplier_wins_even_when_another_name_sorts_first(self):
		# The rank-1 name sorts last alphabetically: row order alone would hand this to the rank-2
		# supplier, which is exactly what the report used to do.
		top = self.offer("ZZ Rank One", price=2.5, rank=1, stock_qty=30)
		self.offer("AA Rank Two", price=2.9, rank=2, stock_qty=30)

		self.make_material_request(10)

		rows = self.rows_for_item()
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["supplier"], top)
		self.assertEqual(flt(rows[0]["allocated_qty"]), 10)

	def test_an_out_of_stock_top_rank_falls_through_to_the_next_supplier(self):
		self.offer("Rank One No Stock", price=2.5, rank=1, stock_qty=0)
		second = self.offer("Rank Two In Stock", price=2.9, rank=2, stock_qty=30)

		self.make_material_request(10)

		rows = self.rows_for_item()
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["supplier"], second)
		self.assertEqual(flt(rows[0]["allocated_qty"]), 10)

	def test_demand_beyond_the_top_supplier_spills_onto_the_next_rank(self):
		top = self.offer("Rank One Short", price=2.5, rank=1, stock_qty=8)
		second = self.offer("Rank Two Rest", price=2.9, rank=2, stock_qty=30)

		self.make_material_request(10)

		rows = sorted(self.rows_for_item(), key=lambda row: row["supplier_rank"])
		self.assertEqual([row["supplier"] for row in rows], [top, second])
		self.assertEqual([flt(row["allocated_qty"]) for row in rows], [8, 2])
