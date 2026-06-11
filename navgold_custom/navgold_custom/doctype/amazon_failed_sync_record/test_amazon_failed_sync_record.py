# Copyright (c) 2025, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.doctype.amazon_failed_sync_record.amazon_failed_sync_record import (
	bulk_retry_failed_syncs,
)


def create_failed_sync_record(status="Failed"):
	record = frappe.new_doc("Amazon Failed Sync Record")
	record.amazon_order_id = frappe.generate_hash(length=12)
	record.status = status
	record.insert()
	return record


class IntegrationTestAmazonFailedSyncRecord(IntegrationTestCase):
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
