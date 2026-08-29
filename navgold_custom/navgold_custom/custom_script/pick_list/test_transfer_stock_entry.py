# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import make_item
from navgold_custom.navgold_custom.custom_script.stock_entry.stock_entry import get_afn_material_issue
from navgold_custom.navgold_custom.custom_script.stock_entry.test_fba_clearing import (
	COMPANY,
	create_test_warehouse,
)

PACK_UOM = "Test Transfer Pack of 6"
PACK_SIZE = 6


class TestTransferStockEntryPartPack(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = COMPANY
		cls.warehouse = create_test_warehouse("Test Pack Fraction Source")
		cls.target_warehouse = create_test_warehouse("Test Pack Fraction AFN")
		cls.configured_afn_warehouse = frappe.db.get_single_value("Amazon SP API Settings", "afn_warehouse")
		make_pack_uom()

	@classmethod
	def tearDownClass(cls):
		frappe.db.set_single_value("Amazon SP API Settings", "afn_warehouse", cls.configured_afn_warehouse)
		super().tearDownClass()

	def setUp(self):
		super().setUp()
		frappe.db.set_single_value("Amazon SP API Settings", "afn_warehouse", self.target_warehouse)
		self.item_code = make_item(f"TEST-PACK-FRACTION-{self._testMethodName}", is_stock_item=1)
		add_pack_conversion(self.item_code)
		receive_stock(self.item_code, 500, self.warehouse, self.company)

	def make_transfer_pick_list(self, rows):
		pick_list = frappe.new_doc("Pick List")
		pick_list.company = self.company
		pick_list.purpose = "Material Transfer"
		pick_list.pick_manually = 1
		for item_code, picked_stock_qty in rows:
			pick_list.append(
				"locations",
				{
					"item_code": item_code,
					"warehouse": self.warehouse,
					"qty": picked_stock_qty / PACK_SIZE,
					"stock_qty": picked_stock_qty,
					"picked_qty": picked_stock_qty,
					"uom": PACK_UOM,
					"stock_uom": "Nos",
					"conversion_factor": PACK_SIZE,
				},
			)
		pick_list.insert()
		pick_list.submit()
		return pick_list

	def get_transfer_stock_entry(self, pick_list):
		return frappe.get_last_doc("Stock Entry", filters={"pick_list": pick_list.name})

	def test_a_part_pack_transfer_moves_exactly_what_was_picked(self):
		pick_list = self.make_transfer_pick_list([(self.item_code, 136)])

		stock_entry = self.get_transfer_stock_entry(pick_list)

		self.assertEqual(len(stock_entry.items), 1)
		self.assertEqual(flt(stock_entry.items[0].transfer_qty), 136)
		self.assertEqual(stock_entry.items[0].uom, "Nos")
		self.assertEqual(flt(stock_entry.items[0].qty), 136)

	def test_the_transfer_stock_entry_is_submitted_into_the_afn_warehouse(self):
		pick_list = self.make_transfer_pick_list([(self.item_code, 136)])

		stock_entry = self.get_transfer_stock_entry(pick_list)

		self.assertEqual(stock_entry.docstatus, 1)
		self.assertEqual(stock_entry.stock_entry_type, "FBA Transfer")
		self.assertEqual(stock_entry.items[0].t_warehouse, self.target_warehouse)
		self.assertEqual(
			flt(frappe.db.get_value("Pick List Item", pick_list.locations[0].name, "transferred_qty")),
			136,
		)

	def test_an_unset_afn_warehouse_blocks_the_submit(self):
		frappe.db.set_single_value("Amazon SP API Settings", "afn_warehouse", None)

		with self.assertRaises(frappe.ValidationError):
			self.make_transfer_pick_list([(self.item_code, 12)])

	def test_the_transferred_stock_is_issued_back_out_of_fba(self):
		pick_list = self.make_transfer_pick_list([(self.item_code, 136)])

		stock_entry = self.get_transfer_stock_entry(pick_list)
		material_issue = frappe.get_doc("Stock Entry", get_afn_material_issue(stock_entry.name))

		self.assertEqual(material_issue.stock_entry_type, "Material Issue")
		self.assertEqual(material_issue.docstatus, 1)
		self.assertEqual(material_issue.items[0].s_warehouse, self.target_warehouse)
		self.assertEqual(flt(material_issue.items[0].transfer_qty), 136)

	def test_a_whole_pack_transfer_keeps_its_pack_uom(self):
		pick_list = self.make_transfer_pick_list([(self.item_code, 132)])

		stock_entry = self.get_transfer_stock_entry(pick_list)

		self.assertEqual(stock_entry.items[0].uom, PACK_UOM)
		self.assertEqual(flt(stock_entry.items[0].qty), 22)
		self.assertEqual(flt(stock_entry.items[0].transfer_qty), 132)

	def test_only_the_part_pack_row_changes_uom(self):
		second_item = make_item(f"TEST-PACK-FRACTION-{self._testMethodName}-2", is_stock_item=1)
		add_pack_conversion(second_item)
		receive_stock(second_item, 500, self.warehouse, self.company)
		pick_list = self.make_transfer_pick_list([(self.item_code, 132), (second_item, 136)])

		stock_entry = self.get_transfer_stock_entry(pick_list)

		rows = {row.item_code: row for row in stock_entry.items}
		self.assertEqual(rows[self.item_code].uom, PACK_UOM)
		self.assertEqual(flt(rows[self.item_code].transfer_qty), 132)
		self.assertEqual(rows[second_item].uom, "Nos")
		self.assertEqual(flt(rows[second_item].transfer_qty), 136)


class TestTransferStockEntryBatches(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = COMPANY
		cls.warehouse = create_test_warehouse("Test Batch Transfer Source")
		cls.target_warehouse = create_test_warehouse("Test Batch Transfer AFN")
		cls.configured_afn_warehouse = frappe.db.get_single_value("Amazon SP API Settings", "afn_warehouse")
		cls.auto_bundle_setting = frappe.db.get_single_value(
			"Stock Settings", "auto_create_serial_and_batch_bundle_for_outward"
		)

	@classmethod
	def tearDownClass(cls):
		frappe.db.set_single_value("Amazon SP API Settings", "afn_warehouse", cls.configured_afn_warehouse)
		frappe.db.set_single_value(
			"Stock Settings", "auto_create_serial_and_batch_bundle_for_outward", cls.auto_bundle_setting
		)
		super().tearDownClass()

	def setUp(self):
		super().setUp()
		frappe.db.set_single_value("Amazon SP API Settings", "afn_warehouse", self.target_warehouse)
		# the FIFO auto-pick only kicks in with this on, which is what broke the picked batch
		frappe.db.set_single_value("Stock Settings", "auto_create_serial_and_batch_bundle_for_outward", 1)
		self.item_code = make_batch_item(f"TEST-BATCH-TRANSFER-{self._testMethodName}")
		self.first_batch = receive_batch_stock(self.item_code, 10, self.warehouse, self.company)
		self.second_batch = receive_batch_stock(self.item_code, 10, self.warehouse, self.company)

	def make_transfer_pick_list(self, rows):
		pick_list = frappe.new_doc("Pick List")
		pick_list.company = self.company
		pick_list.purpose = "Material Transfer"
		pick_list.pick_manually = 1
		for batch_no, picked_qty in rows:
			pick_list.append(
				"locations",
				{
					"item_code": self.item_code,
					"warehouse": self.warehouse,
					"batch_no": batch_no,
					"use_serial_batch_fields": 1,
					"qty": picked_qty,
					"stock_qty": picked_qty,
					"picked_qty": picked_qty,
					"uom": "Nos",
					"stock_uom": "Nos",
					"conversion_factor": 1,
				},
			)
		pick_list.insert()
		pick_list.submit()
		return frappe.get_last_doc("Stock Entry", filters={"pick_list": pick_list.name})

	def assert_rows_moved_their_own_batch(self, stock_entry, expected_rows):
		self.assertEqual([(row.batch_no, flt(row.transfer_qty)) for row in stock_entry.items], expected_rows)
		for row in stock_entry.items:
			self.assertEqual(row.use_serial_batch_fields, 1)
			self.assertEqual(get_bundle_batches(row.serial_and_batch_bundle), [row.batch_no])

	def test_each_transfer_row_moves_the_batch_that_was_picked(self):
		stock_entry = self.make_transfer_pick_list([(self.first_batch, 4), (self.second_batch, 6)])

		self.assert_rows_moved_their_own_batch(stock_entry, [(self.first_batch, 4), (self.second_batch, 6)])

	def test_two_rows_on_the_same_batch_each_keep_that_batch(self):
		stock_entry = self.make_transfer_pick_list(
			[(self.first_batch, 4), (self.second_batch, 3), (self.second_batch, 5)]
		)

		self.assert_rows_moved_their_own_batch(
			stock_entry, [(self.first_batch, 4), (self.second_batch, 3), (self.second_batch, 5)]
		)


def make_batch_item(item_code):
	if frappe.db.exists("Item", item_code):
		return item_code
	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = item_code
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 1
	item.has_batch_no = 1
	item.create_new_batch = 1
	item.batch_number_series = f"{item_code}-.####"
	item.insert()
	return item.name


def receive_batch_stock(item_code, qty, warehouse, company):
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.stock_entry_type = "Material Receipt"
	stock_entry.company = company
	stock_entry.append(
		"items",
		{"item_code": item_code, "qty": qty, "t_warehouse": warehouse, "basic_rate": 10},
	)
	stock_entry.insert()
	stock_entry.submit()
	stock_entry.reload()
	return get_bundle_batches(stock_entry.items[0].serial_and_batch_bundle)[0]


def get_bundle_batches(bundle):
	return frappe.get_all(
		"Serial and Batch Entry", filters={"parent": bundle}, pluck="batch_no", distinct=True
	)


def make_pack_uom():
	if frappe.db.exists("UOM", PACK_UOM):
		return
	uom = frappe.new_doc("UOM")
	uom.uom_name = PACK_UOM
	uom.must_be_whole_number = 0
	uom.insert()


def add_pack_conversion(item_code):
	item = frappe.get_doc("Item", item_code)
	if any(row.uom == PACK_UOM for row in item.uoms):
		return
	item.append("uoms", {"uom": PACK_UOM, "conversion_factor": PACK_SIZE})
	item.save()


def receive_stock(item_code, qty, warehouse, company):
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.stock_entry_type = "Material Receipt"
	stock_entry.company = company
	stock_entry.append(
		"items",
		{"item_code": item_code, "qty": qty, "t_warehouse": warehouse, "basic_rate": 10},
	)
	stock_entry.insert()
	stock_entry.submit()
