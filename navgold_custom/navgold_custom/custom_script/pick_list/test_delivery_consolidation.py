# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate

from navgold_custom.navgold_custom.erpnext_compat import create_pick_list

COMPANY = "Navgold Ltd"
CUSTOMER = "TEST-DC-CUSTOMER"
PACK_UOM = "Test DC Pack of 6"
PACK_SIZE = 6
ITEM_CODE = "TEST-DC-PACK-ITEM"
BATCH_ITEM_CODE = "TEST-DC-BATCH-ITEM"


def create_test_warehouse(warehouse_name):
	abbr = frappe.get_cached_value("Company", COMPANY, "abbr")
	full_name = f"{warehouse_name} - {abbr}"
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
		create_pack_item(ITEM_CODE)
		receive_stock(ITEM_CODE, self.shelf_a, 2)
		receive_stock(ITEM_CODE, self.shelf_b, 4)
		sales_order = create_pack_sales_order(ITEM_CODE)

		pick_list = submit_pick_list_for(sales_order)

		transfer = get_consolidation_transfer(pick_list)
		self.assertIsNotNone(transfer, "consolidation transfer was not created")
		self.assertEqual(sum(flt(row.qty) for row in transfer.items), 6)
		for row in transfer.items:
			self.assertEqual(row.uom, "Nos")
			self.assertEqual(row.t_warehouse, self.consolidation_warehouse)
			self.assertEqual(flt(row.qty) % 1, 0, "transfer must move whole stock units")

		self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Completed")

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
		dn_row = delivery_note_items[0]
		self.assertEqual(flt(dn_row.qty), 1)
		self.assertEqual(flt(dn_row.stock_qty), 6)
		self.assertEqual(dn_row.warehouse, self.consolidation_warehouse)
		self.assertEqual(dn_row.uom, PACK_UOM)

		# the original bug killed the flow at submit (whole-number UOM + over-allowance)
		delivery_note = frappe.get_doc("Delivery Note", dn_row.parent)
		delivery_note.submit()
		self.assertEqual(flt(frappe.db.get_value("Sales Order", sales_order.name, "per_delivered")), 100)
		remaining = frappe.db.get_value(
			"Bin",
			{"item_code": ITEM_CODE, "warehouse": self.consolidation_warehouse},
			"actual_qty",
		)
		self.assertEqual(flt(remaining), 0)

	def test_partial_pick_stages_stock_without_delivery_note(self):
		create_pack_item(ITEM_CODE)
		receive_stock(ITEM_CODE, self.shelf_b, 4)
		sales_order = create_pack_sales_order(ITEM_CODE)

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
			{"item_code": ITEM_CODE, "warehouse": self.consolidation_warehouse},
			"actual_qty",
		)
		self.assertEqual(flt(staged), 4, "remainder must wait in the consolidation warehouse")

	def test_feature_off_keeps_legacy_direct_delivery_note(self):
		frappe.db.set_single_value("Navgold Settings", "delivery_consolidation_warehouse", "")
		create_pack_item(ITEM_CODE)
		receive_stock(ITEM_CODE, self.shelf_a, 2)
		receive_stock(ITEM_CODE, self.shelf_b, 4)
		sales_order = create_pack_sales_order(ITEM_CODE)

		pick_list = submit_pick_list_for(sales_order)

		self.assertIsNone(
			get_consolidation_transfer(pick_list), "no transfer may be created when the feature is off"
		)
		delivery_note_items = get_delivery_note_items(sales_order)
		self.assertEqual(len(delivery_note_items), 2, "legacy path maps one DN row per pick location")
		self.assertEqual({row.warehouse for row in delivery_note_items}, {self.shelf_a, self.shelf_b})

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
