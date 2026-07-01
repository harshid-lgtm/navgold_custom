# Copyright (c) 2025, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.doctype.amazon_failed_sync_record import amazon_failed_sync_record
from navgold_custom.navgold_custom.doctype.amazon_failed_sync_record.amazon_failed_sync_record import (
	bulk_retry_failed_syncs,
	run_bulk_retry,
)


def create_failed_sync_record(status="Failed", amazon_order_id=None):
	record = frappe.new_doc("Amazon Failed Sync Record")
	record.amazon_order_id = amazon_order_id or frappe.generate_hash(length=12)
	record.status = status
	record.insert()
	return record


def create_submitted_amazon_so():
	# Self-seed a submitted Amazon Sales Order (Customer + Item + submit) so the resolution
	# path runs on a bare CI site too — the restored dev site has orders, a fresh CI site does not.
	from frappe.utils import add_days, nowdate

	customer = frappe.new_doc("Customer")
	customer.customer_name = f"Failed Sync Customer {frappe.generate_hash(length=8)}"
	customer.customer_group = frappe.db.get_value(
		"Customer Group", {"is_group": 0}, "name"
	) or frappe.db.get_value("Customer Group", {}, "name")
	customer.territory = frappe.db.get_value("Territory", {"is_group": 0}, "name") or frappe.db.get_value(
		"Territory", {}, "name"
	)
	customer.insert(ignore_permissions=True)

	item = frappe.new_doc("Item")
	item.item_code = f"FAILED-SYNC-{frappe.generate_hash(length=8)}"
	item.item_name = item.item_code
	item.item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
	item.stock_uom = "Nos"
	item.is_stock_item = 1
	item.insert(ignore_permissions=True)

	amazon_order_id = f"AMZ-{frappe.generate_hash(length=10)}"
	so = frappe.new_doc("Sales Order")
	so.customer = customer.name
	so.company = frappe.db.get_value("Company", {}, "name")
	so.transaction_date = nowdate()
	so.delivery_date = add_days(nowdate(), 5)
	so.amazon_order_id = amazon_order_id
	so.append(
		"items",
		{
			"item_code": item.item_code,
			"qty": 1,
			"rate": 10,
			"uom": "Nos",
			"warehouse": frappe.db.get_value("Warehouse", {"is_group": 0, "disabled": 0}, "name"),
			"delivery_date": add_days(nowdate(), 5),
		},
	)
	so.insert(ignore_permissions=True)
	so.submit()
	return frappe._dict(name=so.name, amazon_order_id=amazon_order_id)


class IntegrationTestAmazonFailedSyncRecord(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.existing_so = create_submitted_amazon_so()

	def test_bulk_retry_rejects_empty_selection(self):
		self.assertRaises(frappe.ValidationError, bulk_retry_failed_syncs, [])

	def test_bulk_retry_rejects_non_failed_records(self):
		resolved = create_failed_sync_record(status="Resolved")
		failed = create_failed_sync_record()

		with patch("frappe.enqueue") as mocked_enqueue:
			self.assertRaises(frappe.ValidationError, bulk_retry_failed_syncs, [resolved.name, failed.name])

		mocked_enqueue.assert_not_called()

	def test_bulk_retry_enqueues_failed_records(self):
		first = create_failed_sync_record()
		second = create_failed_sync_record()

		with patch("frappe.enqueue") as mocked_enqueue:
			result = bulk_retry_failed_syncs(frappe.as_json([first.name, second.name]))

		self.assertEqual(result["queued"], 2)
		mocked_enqueue.assert_called_once()
		self.assertCountEqual(mocked_enqueue.call_args.kwargs["record_names"], [first.name, second.name])
		self.assertEqual(mocked_enqueue.call_args.kwargs["queue"], "long")

	def test_retry_fetching_resolves_when_matching_so_exists(self):
		record = create_failed_sync_record(amazon_order_id=self.existing_so.amazon_order_id)
		result = record.retry_fetching()

		self.assertEqual(result, [self.existing_so.name])
		persisted = frappe.db.get_value(
			"Amazon Failed Sync Record", record.name, ["status", "remarks"], as_dict=True
		)
		self.assertEqual(persisted.status, "Resolved")
		self.assertIn(self.existing_so.name, persisted.remarks)

	def test_retry_fetching_no_match_stays_failed(self):
		record = create_failed_sync_record()

		# No matching Sales Order => it hits Amazon's getOrders; mock that boundary to
		# return nothing so the record stays Failed without a live call.
		with patch.object(amazon_failed_sync_record, "get_order", return_value=[]):
			result = record.retry_fetching()

		self.assertEqual(result, [])
		self.assertEqual(frappe.db.get_value("Amazon Failed Sync Record", record.name, "status"), "Failed")

	def test_run_bulk_retry_resolves_matching_record(self):
		record = create_failed_sync_record(amazon_order_id=self.existing_so.amazon_order_id)

		# run_bulk_retry commits per record and paces with sleep; neutralise both so the
		# test transaction rolls back and the run is instant.
		with (
			patch("frappe.db.commit"),
			patch.object(amazon_failed_sync_record.time, "sleep"),
		):
			run_bulk_retry([record.name])

		self.assertEqual(frappe.db.get_value("Amazon Failed Sync Record", record.name, "status"), "Resolved")
