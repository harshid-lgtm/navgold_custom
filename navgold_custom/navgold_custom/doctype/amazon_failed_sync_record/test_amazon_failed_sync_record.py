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


class IntegrationTestAmazonFailedSyncRecord(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# A live restore always carries submitted Amazon Sales Orders; reuse one so the
		# resolution path matches a real row without building a whole SO in the test.
		cls.existing_so = frappe.db.get_value(
			"Sales Order",
			{"docstatus": 1, "amazon_order_id": ["is", "set"]},
			["name", "amazon_order_id"],
			as_dict=True,
		)

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
		if not self.existing_so:
			self.skipTest("No submitted Amazon Sales Order available on this site")

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
		if not self.existing_so:
			self.skipTest("No submitted Amazon Sales Order available on this site")

		record = create_failed_sync_record(amazon_order_id=self.existing_so.amazon_order_id)

		# run_bulk_retry commits per record and paces with sleep; neutralise both so the
		# test transaction rolls back and the run is instant.
		with (
			patch("frappe.db.commit"),
			patch.object(amazon_failed_sync_record.time, "sleep"),
		):
			run_bulk_retry([record.name])

		self.assertEqual(frappe.db.get_value("Amazon Failed Sync Record", record.name, "status"), "Resolved")
