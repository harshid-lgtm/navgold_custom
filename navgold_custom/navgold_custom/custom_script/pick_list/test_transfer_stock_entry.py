# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import make_item
from navgold_custom.navgold_custom.custom_script.pick_list.pick_list_api import (
	create_fba_transfer_stock_entry,
)
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

	def build_stock_entry(self, pick_list):
		stock_entry = frappe.get_doc(create_fba_transfer_stock_entry(pick_list.name))
		for row in stock_entry.items:
			row.t_warehouse = self.target_warehouse
		return stock_entry

	def test_a_part_pack_transfer_moves_exactly_what_was_picked(self):
		pick_list = self.make_transfer_pick_list([(self.item_code, 136)])

		stock_entry = self.build_stock_entry(pick_list)
		stock_entry.save()

		self.assertEqual(len(stock_entry.items), 1)
		self.assertEqual(flt(stock_entry.items[0].transfer_qty), 136)
		self.assertEqual(stock_entry.items[0].uom, "Nos")
		self.assertEqual(flt(stock_entry.items[0].qty), 136)

	def test_a_part_pack_transfer_submits(self):
		pick_list = self.make_transfer_pick_list([(self.item_code, 136)])

		stock_entry = self.build_stock_entry(pick_list)
		stock_entry.save()
		stock_entry.submit()

		self.assertEqual(
			flt(frappe.db.get_value("Pick List Item", pick_list.locations[0].name, "transferred_qty")),
			136,
		)

	def test_a_whole_pack_transfer_keeps_its_pack_uom(self):
		pick_list = self.make_transfer_pick_list([(self.item_code, 132)])

		stock_entry = self.build_stock_entry(pick_list)
		stock_entry.save()

		self.assertEqual(stock_entry.items[0].uom, PACK_UOM)
		self.assertEqual(flt(stock_entry.items[0].qty), 22)
		self.assertEqual(flt(stock_entry.items[0].transfer_qty), 132)

	def test_only_the_part_pack_row_changes_uom(self):
		second_item = make_item(f"TEST-PACK-FRACTION-{self._testMethodName}-2", is_stock_item=1)
		add_pack_conversion(second_item)
		receive_stock(second_item, 500, self.warehouse, self.company)
		pick_list = self.make_transfer_pick_list([(self.item_code, 132), (second_item, 136)])

		stock_entry = self.build_stock_entry(pick_list)
		stock_entry.save()

		rows = {row.item_code: row for row in stock_entry.items}
		self.assertEqual(rows[self.item_code].uom, PACK_UOM)
		self.assertEqual(flt(rows[self.item_code].transfer_qty), 132)
		self.assertEqual(rows[second_item].uom, "Nos")
		self.assertEqual(flt(rows[second_item].transfer_qty), 136)


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
