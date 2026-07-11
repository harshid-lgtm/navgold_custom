# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate

from navgold_custom.navgold_custom.erpnext_compat import create_pick_list, make_delivery_note

COMPANY = "Navgold Ltd"
CUSTOMER = "TEST-DC-CUSTOMER"
PACK_UOM = "Test DC Pack of 6"
PACK_SIZE = 6
BATCH_ITEM_CODE = "TEST-DC-BATCH-ITEM"


def create_test_warehouse(warehouse_name):
	company_abbreviation = frappe.get_cached_value("Company", COMPANY, "abbr")
	full_name = f"{warehouse_name} - {company_abbreviation}"
	if frappe.db.exists("Warehouse", full_name):
		return full_name

	warehouse = frappe.new_doc("Warehouse")
	warehouse.warehouse_name = warehouse_name
	warehouse.company = COMPANY
	warehouse.insert()
	return warehouse.name


def create_pack_uom():
	if not frappe.db.exists("UOM", PACK_UOM):
		uom = frappe.new_doc("UOM")
		uom.uom_name = PACK_UOM
		uom.insert()
	return PACK_UOM


def create_pack_item(item_code, has_batch_no=0):
	if frappe.db.exists("Item", item_code):
		return item_code

	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = item_code
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 1
	if has_batch_no:
		item.has_batch_no = 1
		item.create_new_batch = 1
		item.batch_number_series = "TEST-DC-BATCH-.####"
	item.append("uoms", {"uom": create_pack_uom(), "conversion_factor": PACK_SIZE})
	item.insert()
	return item.name


def create_test_customer():
	if frappe.db.exists("Customer", CUSTOMER):
		return CUSTOMER

	customer = frappe.new_doc("Customer")
	customer.customer_name = CUSTOMER
	customer.customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
	customer.territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
	customer.insert()
	return customer.name


def receive_stock(item_code, warehouse, qty):
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = COMPANY
	stock_entry.purpose = "Material Receipt"
	stock_entry.set_stock_entry_type()
	stock_entry.append(
		"items",
		{
			"item_code": item_code,
			"t_warehouse": warehouse,
			"qty": qty,
			"uom": "Nos",
			"conversion_factor": 1,
			"basic_rate": 10,
		},
	)
	stock_entry.save()
	stock_entry.submit()
	return stock_entry


def create_pack_sales_order(item_code, pack_qty=1):
	sales_order = frappe.new_doc("Sales Order")
	sales_order.customer = create_test_customer()
	sales_order.company = COMPANY
	sales_order.delivery_date = nowdate()
	sales_order.append(
		"items",
		{
			"item_code": item_code,
			"qty": pack_qty,
			"uom": PACK_UOM,
			"conversion_factor": PACK_SIZE,
			"rate": 100,
			"reserve_stock": 1,
		},
	)
	sales_order.save()
	sales_order.submit()
	return sales_order


def submit_pick_list_for(sales_order):
	pick_list = create_pick_list(sales_order.name)
	pick_list.save()
	# the site runs pick lists in scan mode: submit requires every location scanned
	for location in pick_list.locations:
		location.picked_qty = location.stock_qty
	pick_list.save()
	pick_list.submit()
	return pick_list


def get_consolidation_transfer(pick_list):
	names = frappe.get_all("Stock Entry", filters={"pick_list": pick_list.name, "docstatus": 1}, pluck="name")
	return frappe.get_doc("Stock Entry", names[0]) if names else None


def get_delivery_note_items(sales_order):
	return frappe.get_all(
		"Delivery Note Item",
		filters={"against_sales_order": sales_order.name, "docstatus": 0},
		fields=["parent", "qty", "stock_qty", "warehouse", "conversion_factor", "uom"],
	)


class TestDeliveryConsolidation(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.shelf_a = create_test_warehouse("Test DC Shelf A")
		self.shelf_b = create_test_warehouse("Test DC Shelf B")
		self.consolidation_warehouse = create_test_warehouse("Test DC Ready to Ship")
		frappe.db.set_single_value(
			"Navgold Settings", "delivery_consolidation_warehouse", self.consolidation_warehouse
		)

	def test_pack_split_across_shelves_delivers_one_whole_pack(self):
		item_code = "TEST-DC-SPLIT-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 2)
		receive_stock(item_code, self.shelf_b, 4)
		sales_order = create_pack_sales_order(item_code)

		pick_list = submit_pick_list_for(sales_order)

		transfer = get_consolidation_transfer(pick_list)
		self.assertIsNotNone(transfer, "consolidation transfer was not created")
		self.assertEqual(sum(flt(row.qty) for row in transfer.items), 6)
		for row in transfer.items:
			self.assertEqual(row.uom, "Nos")
			self.assertEqual(row.t_warehouse, self.consolidation_warehouse)
			self.assertEqual(flt(row.qty) % 1, 0, "transfer must move whole stock units")

		# staging is not delivery: completion is driven by the Delivery Note submit below
		self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Open")

		reservations = frappe.get_all(
			"Stock Reservation Entry",
			filters={"voucher_no": sales_order.name, "docstatus": 1},
			fields=["warehouse", "reserved_qty"],
		)
		self.assertEqual(len(reservations), 1)
		self.assertEqual(reservations[0].warehouse, self.consolidation_warehouse)
		self.assertEqual(flt(reservations[0].reserved_qty), 6)

		delivery_note_items = get_delivery_note_items(sales_order)
		self.assertEqual(len(delivery_note_items), 1, "expected exactly one whole-pack DN row")
		delivery_note_row = delivery_note_items[0]
		self.assertEqual(flt(delivery_note_row.qty), 1)
		self.assertEqual(flt(delivery_note_row.stock_qty), 6)
		self.assertEqual(delivery_note_row.warehouse, self.consolidation_warehouse)
		self.assertEqual(delivery_note_row.uom, PACK_UOM)

		# the original bug killed the flow at submit (whole-number UOM + over-allowance)
		delivery_note = frappe.get_doc("Delivery Note", delivery_note_row.parent)
		delivery_note.submit()
		self.assertEqual(flt(frappe.db.get_value("Sales Order", sales_order.name, "per_delivered")), 100)
		pick_list_state = frappe.db.get_value(
			"Pick List", pick_list.name, ["status", "per_delivered"], as_dict=True
		)
		self.assertEqual(pick_list_state.status, "Completed")
		self.assertEqual(flt(pick_list_state.per_delivered), 100)
		remaining = frappe.db.get_value(
			"Bin",
			{"item_code": item_code, "warehouse": self.consolidation_warehouse},
			"actual_qty",
		)
		self.assertEqual(flt(remaining), 0)

	def test_partial_pick_stages_stock_without_delivery_note(self):
		item_code = "TEST-DC-PARTIAL-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_b, 4)
		sales_order = create_pack_sales_order(item_code)

		pick_list = submit_pick_list_for(sales_order)

		transfer = get_consolidation_transfer(pick_list)
		self.assertIsNotNone(transfer)
		self.assertEqual(sum(flt(row.qty) for row in transfer.items), 4)

		self.assertFalse(
			get_delivery_note_items(sales_order),
			"a partial pack must not produce a Delivery Note (fractional qty)",
		)
		staged = frappe.db.get_value(
			"Bin",
			{"item_code": item_code, "warehouse": self.consolidation_warehouse},
			"actual_qty",
		)
		self.assertEqual(flt(staged), 4, "remainder must wait in the consolidation warehouse")
		self.assertEqual(
			frappe.db.get_value("Pick List", pick_list.name, "status"),
			"Open",
			"nothing delivered yet, the pick list must stay Open",
		)

	def test_feature_off_keeps_legacy_direct_delivery_note(self):
		frappe.db.set_single_value("Navgold Settings", "delivery_consolidation_warehouse", "")
		item_code = "TEST-DC-LEGACY-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 2)
		receive_stock(item_code, self.shelf_b, 4)
		sales_order = create_pack_sales_order(item_code)

		pick_list = submit_pick_list_for(sales_order)

		self.assertIsNone(
			get_consolidation_transfer(pick_list), "no transfer may be created when the feature is off"
		)
		delivery_note_items = get_delivery_note_items(sales_order)
		self.assertEqual(len(delivery_note_items), 2, "legacy path maps one DN row per pick location")
		self.assertEqual({row.warehouse for row in delivery_note_items}, {self.shelf_a, self.shelf_b})
		# the staging-time Completed stamp is gone and the legacy DN is a draft, so the
		# pick list stays Open here; the cascade guard itself is exercised in
		# test_legacy_pick_list_untouched_by_completion_cascade
		self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Open")

	def test_batch_item_transfer_carries_batches(self):
		create_pack_item(BATCH_ITEM_CODE, has_batch_no=1)
		receive_stock(BATCH_ITEM_CODE, self.shelf_a, 2)
		receive_stock(BATCH_ITEM_CODE, self.shelf_b, 4)
		sales_order = create_pack_sales_order(BATCH_ITEM_CODE)

		pick_list = submit_pick_list_for(sales_order)

		transfer = get_consolidation_transfer(pick_list)
		self.assertIsNotNone(transfer)
		self.assertEqual(sum(flt(row.qty) for row in transfer.items), 6)
		for row in transfer.items:
			self.assertEqual(row.t_warehouse, self.consolidation_warehouse)
			has_batch_info = bool(row.batch_no or row.serial_and_batch_bundle or row.use_serial_batch_fields)
			self.assertTrue(has_batch_info, f"transfer row {row.idx} lost its batch information")

		staged = frappe.db.get_value(
			"Bin",
			{"item_code": BATCH_ITEM_CODE, "warehouse": self.consolidation_warehouse},
			"actual_qty",
		)
		self.assertEqual(flt(staged), 6)

		delivery_note_items = get_delivery_note_items(sales_order)
		self.assertEqual(len(delivery_note_items), 1)
		self.assertEqual(flt(delivery_note_items[0].qty), 1)
		self.assertEqual(delivery_note_items[0].warehouse, self.consolidation_warehouse)

	def test_reservation_disabled_floors_to_whole_packs(self):
		item_code = "TEST-DC-NORESV-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 2)
		receive_stock(item_code, self.shelf_b, 4)
		sales_order = create_pack_sales_order(item_code)

		# change_settings restores the singles value cache too — a bare set_single_value
		# leaks the stale cached value into later tests after rollback
		with self.change_settings("Stock Settings", {"enable_stock_reservation": 0}):
			pick_list = submit_pick_list_for(sales_order)

		transfer = get_consolidation_transfer(pick_list)
		self.assertIsNotNone(transfer)
		self.assertFalse(
			frappe.db.exists("Stock Reservation Entry", {"voucher_no": sales_order.name, "docstatus": 1})
		)

		delivery_note_items = get_delivery_note_items(sales_order)
		self.assertEqual(len(delivery_note_items), 1, "fallback path must still produce one whole-pack row")
		delivery_note_row = delivery_note_items[0]
		self.assertEqual(flt(delivery_note_row.qty), 1)
		self.assertEqual(flt(delivery_note_row.stock_qty), 6)
		self.assertEqual(delivery_note_row.warehouse, self.consolidation_warehouse)

	def test_second_pick_list_ships_only_its_own_reservation(self):
		item_code = "TEST-DC-SEQ-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 6)
		sales_order = create_pack_sales_order(item_code, pack_qty=2)

		first_pick_list = submit_pick_list_for(sales_order)
		first_delivery_items = get_delivery_note_items(sales_order)
		self.assertEqual(len(first_delivery_items), 1)
		self.assertEqual(flt(first_delivery_items[0].qty), 1)
		frappe.get_doc("Delivery Note", first_delivery_items[0].parent).submit()

		# SO-level delivery_status gates completion: half-delivered SO keeps every pick list Open
		self.assertEqual(frappe.db.get_value("Pick List", first_pick_list.name, "status"), "Open")

		receive_stock(item_code, self.shelf_b, 6)
		second_pick_list = submit_pick_list_for(sales_order)
		self.assertNotEqual(first_pick_list.name, second_pick_list.name)

		second_delivery_items = get_delivery_note_items(sales_order)
		self.assertEqual(len(second_delivery_items), 1)
		self.assertEqual(
			flt(second_delivery_items[0].qty),
			1,
			"second DN must ship only the second pick list's pack, not every reservation on the SO",
		)

		frappe.get_doc("Delivery Note", second_delivery_items[0].parent).submit()
		self.assertEqual(flt(frappe.db.get_value("Sales Order", sales_order.name, "per_delivered")), 100)
		self.assertEqual(frappe.db.get_value("Pick List", first_pick_list.name, "status"), "Completed")
		self.assertEqual(frappe.db.get_value("Pick List", second_pick_list.name, "status"), "Completed")

	def test_delivery_note_cancel_reopens_pick_list(self):
		item_code = "TEST-DC-CANCEL-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 2)
		receive_stock(item_code, self.shelf_b, 4)
		sales_order = create_pack_sales_order(item_code)

		pick_list = submit_pick_list_for(sales_order)
		delivery_note_items = get_delivery_note_items(sales_order)
		self.assertEqual(len(delivery_note_items), 1)
		delivery_note = frappe.get_doc("Delivery Note", delivery_note_items[0].parent)
		delivery_note.submit()
		self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Completed")

		delivery_note.reload()
		delivery_note.cancel()

		pick_list_state = frappe.db.get_value(
			"Pick List", pick_list.name, ["status", "per_delivered"], as_dict=True
		)
		self.assertEqual(pick_list_state.status, "Open", "cancelled delivery must reopen the pick list")
		self.assertEqual(flt(pick_list_state.per_delivered), 0)

	def test_multi_sales_order_pick_list_completes_only_when_all_ship(self):
		item_code = "TEST-DC-MULTISO-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 12)
		first_sales_order = create_pack_sales_order(item_code)
		second_sales_order = create_pack_sales_order(item_code)

		pick_list = create_pick_list(first_sales_order.name)
		pick_list = create_pick_list(second_sales_order.name, target_doc=pick_list)
		pick_list.save()
		for location in pick_list.locations:
			location.picked_qty = location.stock_qty
		pick_list.save()
		pick_list.submit()

		first_delivery_items = get_delivery_note_items(first_sales_order)
		second_delivery_items = get_delivery_note_items(second_sales_order)
		self.assertEqual(len(first_delivery_items), 1)
		self.assertEqual(len(second_delivery_items), 1)

		frappe.get_doc("Delivery Note", first_delivery_items[0].parent).submit()
		self.assertEqual(
			frappe.db.get_value("Pick List", pick_list.name, "status"),
			"Open",
			"one undelivered sales order must keep the multi-SO pick list Open",
		)

		frappe.get_doc("Delivery Note", second_delivery_items[0].parent).submit()
		self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Completed")

	def test_legacy_pick_list_untouched_by_completion_cascade(self):
		item_code = "TEST-DC-LEGACY-GUARD-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 6)
		with self.change_settings("Navgold Settings", {"delivery_consolidation_warehouse": ""}):
			sales_order = create_pack_sales_order(item_code)
			pick_list = submit_pick_list_for(sales_order)

		# deliver the SO with an SO-direct DN while the feature is on: the cascade runs,
		# finds this pick list as a candidate, and must skip it (no consolidation transfer)
		delivery_note = make_delivery_note(sales_order.name)
		for row in delivery_note.items:
			row.warehouse = self.shelf_a
		delivery_note.save()
		delivery_note.submit()

		self.assertEqual(
			frappe.db.get_value("Sales Order", sales_order.name, "delivery_status"), "Fully Delivered"
		)
		self.assertEqual(
			frappe.db.get_value("Pick List", pick_list.name, "status"),
			"Open",
			"the cascade must never stamp a legacy pick list Completed",
		)

	def test_restocked_shelf_is_pickable_while_staged(self):
		item_code = "TEST-DC-RESTOCK-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 6)
		first_sales_order = create_pack_sales_order(item_code)
		submit_pick_list_for(first_sales_order)

		# first pick is staged (DN still draft, pick list still Open); a restock on the
		# same shelf must be fully pickable — the staged pick may not claim shelf stock
		receive_stock(item_code, self.shelf_a, 6)
		second_sales_order = create_pack_sales_order(item_code)
		second_pick_list = submit_pick_list_for(second_sales_order)

		self.assertEqual(
			sum(flt(location.picked_qty) for location in second_pick_list.locations),
			6,
			"staged pick list still claims the restocked shelf stock",
		)
