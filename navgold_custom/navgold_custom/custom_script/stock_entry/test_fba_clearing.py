# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, flt, get_datetime, getdate

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import make_item
from navgold_custom.navgold_custom.custom_script.stock_entry.stock_entry import (
	create_afn_material_issue,
)

ITEM_CODE = "TEST-FBA-CLEAR-ITEM"
BACKDATED_ITEM_CODE = "TEST-FBA-CLEAR-BACKDATED-ITEM"
BATCHED_ITEM_CODE = "TEST-FBA-CLEAR-BATCHED-ITEM"


def make_batched_item(item_code):
	item_code = make_item(item_code, is_stock_item=1)
	frappe.db.set_value("Item", item_code, {"has_batch_no": 1, "create_new_batch": 1})
	return item_code


def get_test_company():
	for name in ("Navgold Ltd", "Navgold Test"):
		if frappe.db.exists("Company", name):
			return name
	return frappe.db.get_value("Company", {}, "name")


COMPANY = get_test_company()


def create_test_warehouse(warehouse_name, company=COMPANY):
	company_abbreviation = frappe.get_cached_value("Company", company, "abbr")
	full_name = f"{warehouse_name} - {company_abbreviation}"
	if frappe.db.exists("Warehouse", full_name):
		return full_name

	warehouse = frappe.new_doc("Warehouse")
	warehouse.warehouse_name = warehouse_name
	warehouse.company = company
	warehouse.insert()
	return warehouse.name


def use_afn_warehouse(test_class, warehouse):
	"""Point Amazon SP API Settings at a test warehouse for the class, restoring it afterwards.
	Submitting a transfer Pick List auto-creates an FBA Transfer into whatever this names."""
	configured_warehouse = frappe.db.get_single_value("Amazon SP API Settings", "afn_warehouse")
	frappe.db.set_single_value("Amazon SP API Settings", "afn_warehouse", warehouse)
	test_class.addClassCleanup(
		frappe.db.set_single_value, "Amazon SP API Settings", "afn_warehouse", configured_warehouse
	)


def cancel_auto_transfer(pick_list):
	"""Submitting a transfer Pick List auto-transfers every row into FBA. Tests about the rows that
	stay behind roll that transfer back to leave them outstanding."""
	auto_transfer = frappe.db.get_value("Stock Entry", {"pick_list": pick_list.name, "docstatus": 1})
	if auto_transfer:
		frappe.get_doc("Stock Entry", auto_transfer).cancel()


class TestFBATransferClearing(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.item = make_item(ITEM_CODE, is_stock_item=1)
		cls.backdated_item = make_item(BACKDATED_ITEM_CODE, is_stock_item=1)
		cls.batched_item = make_batched_item(BATCHED_ITEM_CODE)
		cls.source_warehouse = create_test_warehouse("Test FBA Source")
		cls.afn_warehouse = create_test_warehouse("Test FBA AFN")
		cls.spare_warehouse = create_test_warehouse("Test FBA Spare")
		cls.configured_afn_warehouse = frappe.db.get_single_value("Amazon SP API Settings", "afn_warehouse")

	@classmethod
	def tearDownClass(cls):
		frappe.db.set_single_value("Amazon SP API Settings", "afn_warehouse", cls.configured_afn_warehouse)
		super().tearDownClass()

	def setUp(self):
		super().setUp()
		frappe.db.set_single_value("Amazon SP API Settings", "afn_warehouse", self.afn_warehouse)

	def receive_stock(self, qty, item_code=None, posting_date=None, batch_no=None):
		receipt = frappe.new_doc("Stock Entry")
		receipt.stock_entry_type = "Material Receipt"
		receipt.company = COMPANY
		if posting_date:
			receipt.set_posting_time = 1
			receipt.posting_date = posting_date
			receipt.posting_time = "00:00:01"
		receipt.append(
			"items",
			{
				"item_code": item_code or self.item,
				"qty": qty,
				"t_warehouse": self.source_warehouse,
				"basic_rate": 100,
				"use_serial_batch_fields": 1 if batch_no else 0,
				"batch_no": batch_no,
			},
		)
		receipt.insert()
		receipt.submit()
		return receipt

	def make_transfer(
		self,
		rows,
		item_code=None,
		stock_entry_type="FBA Transfer",
		posting_date=None,
		posting_time=None,
	):
		transfer = frappe.new_doc("Stock Entry")
		transfer.stock_entry_type = stock_entry_type
		transfer.company = COMPANY
		if posting_date:
			transfer.set_posting_time = 1
			transfer.posting_date = posting_date
			transfer.posting_time = posting_time

		for qty, target_warehouse, *batch in rows:
			batch_no = batch[0] if batch else None
			transfer.append(
				"items",
				{
					"item_code": item_code or self.item,
					"qty": qty,
					"s_warehouse": self.source_warehouse,
					"t_warehouse": target_warehouse,
					"use_serial_batch_fields": 1 if batch_no else 0,
					"batch_no": batch_no,
				},
			)

		transfer.insert()
		transfer.submit()
		return transfer

	def get_material_issue(self, transfer_name):
		name = frappe.db.get_value("Stock Entry", {"custom_fba_transfer_entry": transfer_name})
		return frappe.get_doc("Stock Entry", name) if name else None

	def afn_qty(self, item_code=None):
		return flt(
			frappe.db.get_value(
				"Bin",
				{"item_code": item_code or self.item, "warehouse": self.afn_warehouse},
				"actual_qty",
			)
		)

	def test_submitting_fba_transfer_issues_the_stock_back_out_of_afn(self):
		self.receive_stock(10)
		opening_afn_qty = self.afn_qty()

		transfer = self.make_transfer([(10, self.afn_warehouse)])
		issue = self.get_material_issue(transfer.name)

		self.assertIsNotNone(issue)
		self.assertEqual(issue.docstatus, 1)
		self.assertEqual(issue.stock_entry_type, "Material Issue")
		self.assertEqual(issue.purpose, "Material Issue")
		self.assertEqual(issue.from_warehouse, self.afn_warehouse)
		self.assertEqual(len(issue.items), 1)
		self.assertEqual(issue.items[0].s_warehouse, self.afn_warehouse)
		self.assertFalse(issue.items[0].t_warehouse)
		self.assertEqual(flt(issue.items[0].qty), 10)

		self.assertEqual(self.afn_qty(), opening_afn_qty)

	def test_material_issue_posts_one_second_after_the_transfer(self):
		self.receive_stock(4)
		transfer = self.make_transfer([(4, self.afn_warehouse)])
		issue = self.get_material_issue(transfer.name)

		self.assertTrue(issue.set_posting_time)
		self.assertEqual(
			get_datetime(f"{issue.posting_date} {issue.posting_time}"),
			add_to_date(get_datetime(f"{transfer.posting_date} {transfer.posting_time}"), seconds=1),
		)

	def test_material_issue_is_not_created_twice(self):
		self.receive_stock(3)
		transfer = self.make_transfer([(3, self.afn_warehouse)])

		create_afn_material_issue(transfer, "on_submit")

		self.assertEqual(frappe.db.count("Stock Entry", {"custom_fba_transfer_entry": transfer.name}), 1)

	def test_plain_material_transfer_is_left_alone(self):
		self.receive_stock(5)
		opening_afn_qty = self.afn_qty()

		transfer = self.make_transfer([(5, self.afn_warehouse)], stock_entry_type="Material Transfer")

		self.assertIsNone(self.get_material_issue(transfer.name))
		self.assertEqual(self.afn_qty(), opening_afn_qty + 5)

	def test_only_rows_landing_in_afn_are_mirrored(self):
		self.receive_stock(7)
		opening_afn_qty = self.afn_qty()

		transfer = self.make_transfer([(4, self.afn_warehouse), (3, self.spare_warehouse)])
		issue = self.get_material_issue(transfer.name)

		self.assertEqual(len(issue.items), 1)
		self.assertEqual(flt(issue.items[0].qty), 4)
		self.assertEqual(issue.items[0].s_warehouse, self.afn_warehouse)
		self.assertEqual(self.afn_qty(), opening_afn_qty)

	def test_material_issue_mirrors_each_batch_of_the_same_item(self):
		batches = []
		for suffix in ("A", "B"):
			batch = frappe.new_doc("Batch")
			batch.item = self.batched_item
			batch.batch_id = f"{BATCHED_ITEM_CODE}-{frappe.generate_hash(length=6)}-{suffix}"
			batch.insert()
			batches.append(batch.name)
			self.receive_stock(6, item_code=self.batched_item, batch_no=batch.name)

		opening_afn_qty = self.afn_qty(item_code=self.batched_item)
		transfer = self.make_transfer(
			[(6, self.afn_warehouse, batches[0]), (6, self.afn_warehouse, batches[1])],
			item_code=self.batched_item,
		)
		issue = self.get_material_issue(transfer.name)

		self.assertEqual(issue.docstatus, 1)
		self.assertEqual([row.batch_no for row in issue.items], batches)
		self.assertEqual(self.afn_qty(item_code=self.batched_item), opening_afn_qty)

	def test_material_issue_never_posts_into_the_next_day(self):
		late_night = add_to_date(getdate(), days=-1)
		self.receive_stock(8, item_code=self.backdated_item, posting_date=late_night)

		transfer = self.make_transfer(
			[(8, self.afn_warehouse)],
			item_code=self.backdated_item,
			posting_date=late_night,
			posting_time="23:59:59.500000",
		)
		issue = self.get_material_issue(transfer.name)

		self.assertEqual(getdate(issue.posting_date), getdate(late_night))
		self.assertGreater(
			get_datetime(f"{issue.posting_date} {issue.posting_time}"),
			get_datetime(f"{transfer.posting_date} {transfer.posting_time}"),
		)
		self.assertEqual(self.afn_qty(item_code=self.backdated_item), 0)

	def test_missing_afn_warehouse_setting_blocks_the_transfer(self):
		frappe.db.set_single_value("Amazon SP API Settings", "afn_warehouse", None)
		self.receive_stock(2)

		with self.assertRaises(frappe.ValidationError):
			self.make_transfer([(2, self.afn_warehouse)])

	def test_cancelling_the_transfer_also_cancels_the_material_issue(self):
		self.receive_stock(6)
		opening_afn_qty = self.afn_qty()
		transfer = self.make_transfer([(6, self.afn_warehouse)])

		transfer.cancel()

		self.assertEqual(self.get_material_issue(transfer.name).docstatus, 2)
		self.assertEqual(self.afn_qty(), opening_afn_qty)
