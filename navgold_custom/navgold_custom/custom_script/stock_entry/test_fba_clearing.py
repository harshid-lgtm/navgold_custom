# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, flt, get_datetime

from navgold_custom.navgold_custom.custom_script.stock_entry.stock_entry import (
	build_afn_clearing_issue,
	create_afn_clearing_issue,
)

ITEM_CODE = "TEST-FBA-CLEAR-ITEM"


def get_test_company():
	# local restore carries "Navgold Ltd"; the bare CI site's before_tests creates "Navgold Test"
	for name in ("Navgold Ltd", "Navgold Test"):
		if frappe.db.exists("Company", name):
			return name
	return frappe.db.get_value("Company", {}, "name")


COMPANY = get_test_company()


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


def create_test_item():
	if frappe.db.exists("Item", ITEM_CODE):
		return ITEM_CODE

	item = frappe.new_doc("Item")
	item.item_code = ITEM_CODE
	item.item_name = ITEM_CODE
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 1
	item.insert()
	return item.name


class TestFBATransferClearing(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.item = create_test_item()
		cls.source_warehouse = create_test_warehouse("Test FBA Source")
		cls.afn_warehouse = create_test_warehouse("Test FBA AFN")

	def setUp(self):
		super().setUp()
		frappe.db.set_single_value("Amazon SP API Settings", "afn_warehouse", self.afn_warehouse)

	def receive_stock(self, qty):
		receipt = frappe.new_doc("Stock Entry")
		receipt.stock_entry_type = "Material Receipt"
		receipt.company = COMPANY
		receipt.append(
			"items",
			{"item_code": self.item, "qty": qty, "t_warehouse": self.source_warehouse, "basic_rate": 100},
		)
		receipt.insert()
		receipt.submit()
		return receipt

	def make_transfer(self, qty, stock_entry_type="FBA Transfer"):
		transfer = frappe.new_doc("Stock Entry")
		transfer.stock_entry_type = stock_entry_type
		transfer.company = COMPANY
		transfer.append(
			"items",
			{
				"item_code": self.item,
				"qty": qty,
				"s_warehouse": self.source_warehouse,
				"t_warehouse": self.afn_warehouse,
			},
		)
		transfer.insert()
		transfer.submit()
		return transfer

	def get_clearing_issue(self, transfer_name):
		name = frappe.db.get_value("Stock Entry", {"custom_fba_transfer_entry": transfer_name})
		return frappe.get_doc("Stock Entry", name) if name else None

	def afn_qty(self):
		return flt(
			frappe.db.get_value(
				"Bin", {"item_code": self.item, "warehouse": self.afn_warehouse}, "actual_qty"
			)
		)

	def test_submitting_fba_transfer_issues_the_stock_back_out_of_afn(self):
		self.receive_stock(10)
		opening_afn_qty = self.afn_qty()

		transfer = self.make_transfer(10)
		issue = self.get_clearing_issue(transfer.name)

		self.assertIsNotNone(issue)
		self.assertEqual(issue.docstatus, 1)
		self.assertEqual(issue.stock_entry_type, "Material Issue")
		self.assertEqual(issue.purpose, "Material Issue")
		self.assertEqual(issue.from_warehouse, self.afn_warehouse)
		self.assertEqual(len(issue.items), 1)
		self.assertEqual(issue.items[0].s_warehouse, self.afn_warehouse)
		self.assertFalse(issue.items[0].t_warehouse)
		self.assertEqual(flt(issue.items[0].qty), 10)

		# the transfer put 10 in and the issue took 10 straight back out
		self.assertEqual(self.afn_qty(), opening_afn_qty)

	def test_clearing_issue_posts_one_second_after_the_transfer(self):
		self.receive_stock(4)
		transfer = self.make_transfer(4)
		issue = self.get_clearing_issue(transfer.name)

		self.assertTrue(issue.set_posting_time)
		self.assertEqual(
			get_datetime(f"{issue.posting_date} {issue.posting_time}"),
			add_to_date(get_datetime(f"{transfer.posting_date} {transfer.posting_time}"), seconds=1),
		)

	def test_clearing_issue_is_not_created_twice(self):
		self.receive_stock(3)
		transfer = self.make_transfer(3)

		create_afn_clearing_issue(transfer)

		self.assertEqual(frappe.db.count("Stock Entry", {"custom_fba_transfer_entry": transfer.name}), 1)

	def test_plain_material_transfer_is_left_alone(self):
		self.receive_stock(5)
		# Bin qty is asserted as a delta throughout: stock-ledger writes commit mid-test, so the
		# warehouse carries over whatever earlier tests in this class left behind.
		opening_afn_qty = self.afn_qty()

		transfer = self.make_transfer(5, stock_entry_type="Material Transfer")

		self.assertIsNone(self.get_clearing_issue(transfer.name))
		self.assertEqual(self.afn_qty(), opening_afn_qty + 5)

	def test_rows_not_landing_in_afn_are_skipped(self):
		transfer = frappe.new_doc("Stock Entry")
		transfer.name = "SE-UNSAVED"
		transfer.company = COMPANY
		transfer.posting_date = "2026-07-31"
		transfer.posting_time = "10:00:00"
		transfer.append(
			"items",
			{
				"item_code": self.item,
				"qty": 1,
				"s_warehouse": self.afn_warehouse,
				"t_warehouse": self.source_warehouse,
			},
		)

		self.assertIsNone(build_afn_clearing_issue(transfer, self.afn_warehouse))

	def test_missing_afn_warehouse_setting_blocks_the_transfer(self):
		frappe.db.set_single_value("Amazon SP API Settings", "afn_warehouse", None)
		self.receive_stock(2)

		with self.assertRaises(frappe.ValidationError):
			self.make_transfer(2)

	def test_cancelling_the_transfer_also_cancels_the_clearing_issue(self):
		self.receive_stock(6)
		opening_afn_qty = self.afn_qty()
		transfer = self.make_transfer(6)

		transfer.cancel()

		self.assertEqual(self.get_clearing_issue(transfer.name).docstatus, 2)
		self.assertEqual(self.afn_qty(), opening_afn_qty)
