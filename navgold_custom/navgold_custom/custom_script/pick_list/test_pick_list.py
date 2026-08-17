# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import (
	get_stock_warehouse,
	make_item,
)
from navgold_custom.navgold_custom.custom_script.pick_list.pick_list import (
	get_open_pick_list_claims,
	get_pickable_stock_qty,
	get_stock_fulfillment,
	get_whole_number_uoms,
)
from navgold_custom.navgold_custom.custom_script.stock_entry.test_fba_clearing import (
	cancel_auto_transfer,
	create_test_warehouse,
	use_afn_warehouse,
)

# erpnext.stock.doctype.warehouse.test_warehouse.create_warehouse is deliberately NOT
# reused: importing erpnext test modules triggers their test-record chain, which creates
# a Supplier that fails this app's lead-time validation hook during test discovery.


def receive_stock(item_code, qty_per_warehouse, warehouses, company):
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.stock_entry_type = "Material Receipt"
	stock_entry.company = company
	for warehouse in warehouses:
		stock_entry.append(
			"items",
			{
				"item_code": item_code,
				"qty": qty_per_warehouse,
				"t_warehouse": warehouse,
				"basic_rate": 10,
			},
		)
	stock_entry.insert()
	stock_entry.submit()


def make_pick_list(company, item_code, qty):
	pick_list = frappe.new_doc("Pick List")
	pick_list.company = company
	pick_list.purpose = "Material Transfer"
	pick_list.append(
		"locations",
		{
			"item_code": item_code,
			"qty": qty,
			"stock_qty": qty,
			"uom": "Nos",
			"stock_uom": "Nos",
			"conversion_factor": 1,
		},
	)
	pick_list.save()
	return pick_list


class TestPickListWalkRouteSort(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = (
			frappe.defaults.get_global_default("company")
			or frappe.get_all("Company", limit=1, pluck="name")[0]
		)
		parent_warehouse = frappe.get_all(
			"Warehouse",
			filters={"company": cls.company, "is_group": 1},
			limit=1,
			pluck="name",
		)[0]

		def make_test_warehouse(warehouse_name, walk_route):
			warehouse = frappe.new_doc("Warehouse")
			warehouse.warehouse_name = warehouse_name
			warehouse.company = cls.company
			warehouse.parent_warehouse = parent_warehouse
			warehouse.custom_walk_route = walk_route
			warehouse.insert()
			return warehouse.name

		cls.warehouse_late = make_test_warehouse("Walk Route Test Late", 30)
		cls.warehouse_mid = make_test_warehouse("Walk Route Test Mid", 10.5)
		cls.warehouse_early = make_test_warehouse("Walk Route Test Early", 10)
		cls.warehouse_unrouted = make_test_warehouse("Walk Route Test Unrouted", 0)

		# Receipt row order deliberately differs from walk-route order so FIFO
		# allocation alone cannot produce the expected sequence.
		cls.routed_item = make_item("WALK-ROUTE-ITEM", 1)
		receive_stock(
			cls.routed_item, 5, [cls.warehouse_late, cls.warehouse_mid, cls.warehouse_early], cls.company
		)

		cls.partly_routed_item = make_item("WALK-ROUTE-ITEM-UNROUTED", 1)
		receive_stock(cls.partly_routed_item, 5, [cls.warehouse_unrouted, cls.warehouse_late], cls.company)

	def test_locations_sorted_by_walk_route_with_decimal_routes(self):
		pick_list = make_pick_list(self.company, self.routed_item, 12)

		warehouses = [row.warehouse for row in pick_list.locations]
		self.assertEqual(warehouses, [self.warehouse_early, self.warehouse_mid, self.warehouse_late])
		self.assertEqual([row.custom_walk_route for row in pick_list.locations], [10, 10.5, 30])
		self.assertEqual([row.idx for row in pick_list.locations], [1, 2, 3])

		# The order must survive a reload — a sort that skips idx renumbering
		# only reorders in memory and reverts on the next fetch.
		reloaded = frappe.get_doc("Pick List", pick_list.name)
		self.assertEqual(
			[row.warehouse for row in reloaded.locations],
			[self.warehouse_early, self.warehouse_mid, self.warehouse_late],
		)

	def test_unrouted_warehouse_sorts_last(self):
		pick_list = make_pick_list(self.company, self.partly_routed_item, 10)

		warehouses = [row.warehouse for row in pick_list.locations]
		self.assertEqual(warehouses, [self.warehouse_late, self.warehouse_unrouted])


class TestPickListOpenOrderFilter(IntegrationTestCase):
	def setUp(self):
		settings = frappe.get_single("Amazon SP API Settings")
		self.company = settings.company
		self.warehouse = settings.warehouse
		self.customer = frappe.db.get_value("Customer", {}, "name")
		if not (self.company and self.customer and self.warehouse):
			self.skipTest("Amazon SP API Settings has no company/warehouse configured")

		self.item_code = make_item("TEST-PICKABLE-ITEM", {"is_stock_item": 0})
		self.created = []
		self.pick_lists = []

	def tearDown(self):
		for name in self.pick_lists:
			frappe.delete_doc("Pick List", name, force=True, ignore_permissions=True)
		for name in self.created:
			sales_order = frappe.get_doc("Sales Order", name)
			if sales_order.docstatus == 1:
				sales_order.cancel()
			sales_order.delete(force=True)

	def make_amazon_sales_order(self, amazon_order_status):
		sales_order = frappe.new_doc("Sales Order")
		sales_order.customer = self.customer
		sales_order.company = self.company
		sales_order.transaction_date = frappe.utils.nowdate()
		sales_order.delivery_date = frappe.utils.nowdate()
		sales_order.amazon_order_id = f"PICKFILTER-{amazon_order_status}"
		sales_order.amazon_order_status = amazon_order_status
		sales_order.fulfillment_channel = "MFN"
		sales_order.append(
			"items",
			{
				"item_code": self.item_code,
				"qty": 1,
				"rate": 10,
				"warehouse": self.warehouse,
				"delivery_date": frappe.utils.nowdate(),
			},
		)
		sales_order.flags.ignore_mandatory = True
		sales_order.save(ignore_permissions=True)
		sales_order.submit()
		self.created.append(sales_order.name)
		return sales_order.name

	def get_offered_orders(self):
		from navgold_custom.navgold_custom.custom_script.pick_list.pick_list_api import (
			get_open_sales_orders_for_delivery,
		)

		offered = get_open_sales_orders_for_delivery(company=self.company)
		return {row["sales_order"] for row in offered}

	def test_only_orders_still_owing_a_dispatch_are_offered(self):
		unshipped = self.make_amazon_sales_order("Unshipped")
		partially_shipped = self.make_amazon_sales_order("PartiallyShipped")
		shipped = self.make_amazon_sales_order("Shipped")
		pending = self.make_amazon_sales_order("Pending")

		offered = self.get_offered_orders()

		self.assertIn(unshipped, offered)
		self.assertIn(partially_shipped, offered)
		self.assertNotIn(shipped, offered, "a shipped order has already gone")
		self.assertNotIn(pending, offered, "payment is not authorised, so it must not be picked")

	def make_draft_pick_list(self, sales_order_name):
		pick_list = frappe.new_doc("Pick List")
		pick_list.company = self.company
		pick_list.purpose = "Delivery"
		pick_list.pick_manually = 1
		pick_list.append(
			"locations",
			{
				"item_code": self.item_code,
				"qty": 1,
				"stock_qty": 1,
				"warehouse": self.warehouse,
				"sales_order": sales_order_name,
			},
		)
		pick_list.flags.ignore_mandatory = True
		pick_list.save(ignore_permissions=True)
		self.pick_lists.append(pick_list.name)
		return pick_list.name

	def test_an_order_on_a_draft_pick_list_is_not_offered_again(self):
		claimed = self.make_amazon_sales_order("Unshipped")
		untouched = self.make_amazon_sales_order("PartiallyShipped")

		self.make_draft_pick_list(claimed)

		offered = self.get_offered_orders()

		self.assertNotIn(claimed, offered, "a draft Pick List already claims this order")
		self.assertIn(untouched, offered)

	def test_a_non_amazon_order_is_still_offered(self):
		sales_order = frappe.new_doc("Sales Order")
		sales_order.customer = self.customer
		sales_order.company = self.company
		sales_order.transaction_date = frappe.utils.nowdate()
		sales_order.delivery_date = frappe.utils.nowdate()
		sales_order.append(
			"items",
			{
				"item_code": self.item_code,
				"qty": 1,
				"rate": 10,
				"warehouse": self.warehouse,
				"delivery_date": frappe.utils.nowdate(),
			},
		)
		sales_order.flags.ignore_mandatory = True
		sales_order.save(ignore_permissions=True)
		sales_order.submit()
		self.created.append(sales_order.name)

		self.assertIn(sales_order.name, self.get_offered_orders())


def make_uom(uom_name, must_be_whole_number):
	if frappe.db.exists("UOM", uom_name):
		return uom_name
	uom = frappe.new_doc("UOM")
	uom.uom_name = uom_name
	uom.must_be_whole_number = must_be_whole_number
	uom.insert()
	return uom.name


def make_packed_item(item_code, stock_uom, pack_uom, conversion_factor):
	if frappe.db.exists("Item", item_code):
		return item_code
	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = item_code
	item.item_group = "All Item Groups"
	item.stock_uom = stock_uom
	item.is_stock_item = 1
	item.append("uoms", {"uom": pack_uom, "conversion_factor": conversion_factor})
	item.insert()
	return item.name


class TestPickListWholeStockUnits(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = (
			frappe.defaults.get_global_default("company")
			or frappe.get_all("Company", limit=1, pluck="name")[0]
		)
		cls.pack_uom = make_uom("Whole Units Test Pack of 6", must_be_whole_number=0)
		cls.whole_uom = make_uom("Whole Units Test Each", must_be_whole_number=1)
		cls.fractional_uom = make_uom("Whole Units Test Litre", must_be_whole_number=0)

		cls.whole_item = make_packed_item(
			"WHOLE-UNIT-PACK-ITEM", cls.whole_uom, cls.pack_uom, conversion_factor=6
		)
		cls.fractional_item = make_packed_item(
			"FRACTIONAL-UNIT-PACK-ITEM", cls.fractional_uom, cls.pack_uom, conversion_factor=6
		)

		cls.warehouse = frappe.get_all(
			"Warehouse",
			filters={"company": cls.company, "is_group": 0, "is_rejected_warehouse": 0, "disabled": 0},
			limit=1,
			pluck="name",
		)[0]
		receive_stock(cls.whole_item, 50, [cls.warehouse], cls.company)
		receive_stock(cls.fractional_item, 50, [cls.warehouse], cls.company)
		use_afn_warehouse(cls, create_test_warehouse("Test Whole Stock Units AFN", cls.company))

		get_whole_number_uoms.clear_cache()

	def make_single_row_pick_list(self, item_code, stock_uom, qty, stock_qty, picked_qty):
		pick_list = frappe.new_doc("Pick List")
		pick_list.company = self.company
		pick_list.purpose = "Material Transfer"
		pick_list.pick_manually = 1
		pick_list.append(
			"locations",
			{
				"item_code": item_code,
				"qty": qty,
				"stock_qty": stock_qty,
				"picked_qty": picked_qty,
				"warehouse": self.warehouse,
				"uom": self.pack_uom,
				"stock_uom": stock_uom,
				"conversion_factor": 6,
			},
		)
		pick_list.save()
		return pick_list

	def make_dusty_pick_list(self, item_code, stock_uom):
		return self.make_single_row_pick_list(item_code, stock_uom, 0.833, 0.833 * 6, 0.833 * 6)

	def test_fractional_stock_qty_is_snapped_to_whole_stock_units(self):
		pick_list = self.make_dusty_pick_list(self.whole_item, self.whole_uom)

		row = pick_list.locations[0]
		self.assertEqual(row.stock_qty, 5)
		self.assertEqual(row.picked_qty, 5)
		self.assertEqual(row.qty * row.conversion_factor, row.stock_qty)

	def test_a_genuine_half_unit_is_floored_never_rounded_up(self):
		pick_list = self.make_single_row_pick_list(self.whole_item, self.whole_uom, 0.25, 1.5, 1.5)

		row = pick_list.locations[0]
		self.assertEqual(row.stock_qty, 1, "rounding 1.5 up to 2 would claim stock the shelf never held")
		self.assertEqual(row.picked_qty, 1)

	def test_a_half_unit_below_one_is_floored_to_nothing(self):
		pick_list = self.make_single_row_pick_list(self.whole_item, self.whole_uom, 0.083, 0.5, 0.5)

		row = pick_list.locations[0]
		self.assertEqual(row.stock_qty, 0)
		self.assertEqual(row.picked_qty, 0)

	def test_dust_already_stored_is_snapped_on_submit_without_a_save(self):
		pick_list = self.make_dusty_pick_list(self.whole_item, self.whole_uom)
		for row in pick_list.locations:
			frappe.db.set_value(
				"Pick List Item",
				row.name,
				{"qty": 0.833, "stock_qty": 4.998, "picked_qty": 4.998},
				update_modified=False,
			)

		stored = frappe.get_doc("Pick List", pick_list.name)
		stored.submit()

		self.assertEqual(stored.locations[0].stock_qty, 5)
		self.assertEqual(stored.locations[0].picked_qty, 5)

	def test_snapped_quantities_survive_a_reload(self):
		pick_list = self.make_dusty_pick_list(self.whole_item, self.whole_uom)

		reloaded = frappe.get_doc("Pick List", pick_list.name)
		self.assertEqual(reloaded.locations[0].stock_qty, 5)
		self.assertEqual(reloaded.locations[0].picked_qty, 5)

	def test_fraction_tolerant_stock_uom_keeps_its_fraction(self):
		pick_list = self.make_dusty_pick_list(self.fractional_item, self.fractional_uom)

		row = pick_list.locations[0]
		self.assertEqual(row.stock_qty, 0.833 * 6)
		self.assertEqual(row.picked_qty, 0.833 * 6)
		self.assertEqual(row.qty, 0.833)


class TestPickListStockFulfillment(IntegrationTestCase):
	def setUp(self):
		self.warehouse, self.company = get_stock_warehouse()
		self.customer = frappe.db.get_value("Customer", {}, "name")
		if not self.customer:
			self.skipTest("No Customer on test site")

		self.item_code = make_item("TEST-FULFILLMENT-ITEM", 1)
		self.available_qty = flt(get_pickable_stock_qty({self.item_code}, self.company).get(self.item_code))
		if self.available_qty < 1:
			receive_stock(self.item_code, 1, [self.warehouse], self.company)
			self.available_qty = 1

		self.sales_orders = []

	def tearDown(self):
		for name in self.sales_orders:
			sales_order = frappe.get_doc("Sales Order", name)
			if sales_order.docstatus == 1:
				sales_order.cancel()
			sales_order.delete(force=True)

	def make_sales_order(self, qty):
		sales_order = frappe.new_doc("Sales Order")
		sales_order.customer = self.customer
		sales_order.company = self.company
		sales_order.transaction_date = frappe.utils.nowdate()
		sales_order.delivery_date = frappe.utils.nowdate()
		sales_order.append(
			"items",
			{
				"item_code": self.item_code,
				"qty": qty,
				"rate": 10,
				"warehouse": self.warehouse,
				"delivery_date": frappe.utils.nowdate(),
			},
		)
		sales_order.flags.ignore_mandatory = True
		sales_order.save(ignore_permissions=True)
		sales_order.submit()
		self.sales_orders.append(sales_order.name)
		return sales_order.name

	def test_the_shelf_is_claimed_by_the_first_order_only(self):
		first = self.make_sales_order(self.available_qty)
		second = self.make_sales_order(self.available_qty)

		fulfillment = get_stock_fulfillment([first, second], self.company)

		self.assertTrue(fulfillment[first], "the whole shelf covers this order exactly")
		self.assertFalse(fulfillment[second], "the stock the first order takes is gone")

	def test_an_order_wanting_more_than_the_shelf_holds_leaves_it_for_the_next(self):
		short = self.make_sales_order(self.available_qty + 1)
		coverable = self.make_sales_order(self.available_qty)

		fulfillment = get_stock_fulfillment([short, coverable], self.company)

		self.assertFalse(fulfillment[short])
		self.assertTrue(fulfillment[coverable], "a dropped order consumes nothing")


class TestTransferPickListClaims(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.warehouse, cls.company = get_stock_warehouse()
		cls.item_code = make_item("TEST-TRANSFER-CLAIM-ITEM", 1)
		receive_stock(cls.item_code, 60, [cls.warehouse], cls.company)
		use_afn_warehouse(cls, create_test_warehouse("Test Transfer Claim AFN", cls.company))

	def make_transfer_pick_list(self, picked_qty, transferred_qty):
		pick_list = frappe.new_doc("Pick List")
		pick_list.company = self.company
		pick_list.purpose = "Material Transfer"
		pick_list.pick_manually = 1
		pick_list.append(
			"locations",
			{
				"item_code": self.item_code,
				"qty": picked_qty,
				"stock_qty": picked_qty,
				"picked_qty": picked_qty,
				"warehouse": self.warehouse,
				"uom": "Nos",
				"stock_uom": "Nos",
				"conversion_factor": 1,
			},
		)
		pick_list.save()
		pick_list.submit()
		cancel_auto_transfer(pick_list)

		# erpnext writes transferred_qty when the Stock Entry submits, and leaves delivered_qty at 0
		# for every non-Delivery purpose. Holding the status at Partially Transferred is the state a
		# pick list sits in while one of its rows is still outstanding.
		pick_list.locations[0].db_set("transferred_qty", transferred_qty)
		pick_list.db_set("status", "Partially Transferred")
		self.addCleanup(pick_list.cancel)
		return pick_list

	def test_a_transferred_row_stops_claiming_the_shelf(self):
		self.make_transfer_pick_list(picked_qty=24, transferred_qty=24)

		claims = get_open_pick_list_claims([self.item_code], [self.warehouse])

		self.assertFalse(
			flt(claims.get(self.item_code)),
			"the stock entry already moved this qty off the shelf, so claiming it charges it twice",
		)

	def test_an_untransferred_row_still_claims_the_shelf(self):
		self.make_transfer_pick_list(picked_qty=24, transferred_qty=9)

		claims = get_open_pick_list_claims([self.item_code], [self.warehouse])

		self.assertEqual(flt(claims.get(self.item_code)), 15)
