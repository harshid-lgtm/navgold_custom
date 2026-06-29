# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing import manual_price_push
from navgold_custom.navgold_custom.doctype.amazon_bulk_price_push import amazon_bulk_price_push
from navgold_custom.navgold_custom.doctype.amazon_bulk_price_push.amazon_bulk_price_push import (
	fetch_pricing_for_bulk,
)

GOOD_ROWS = [
	{"sku": "BULK-A", "min_price": 8.86, "max_price": 12.0, "our_price": 9.5},
	{"sku": "BULK-B", "min_price": 5.0, "max_price": 10.0, "our_price": 7.0},
]


class TestBulkPricePush(IntegrationTestCase):
	def make_doc(self, rows=None):
		doc = frappe.new_doc("Amazon Bulk Price Push")
		for row in rows if rows is not None else GOOD_ROWS:
			doc.append("rows", row)
		doc.insert()
		return doc

	def test_get_updates_shapes_rows(self):
		doc = self.make_doc()
		updates = doc.get_updates()
		self.assertEqual([update["sku"] for update in updates], ["BULK-A", "BULK-B"])
		self.assertEqual(updates[0], {"sku": "BULK-A", "min": 8.86, "max": 12.0, "our_price": 9.5})

	def test_add_pricing_rows_dedupes_by_sku(self):
		doc = self.make_doc()
		added = doc.add_pricing_rows(
			[
				{"sku": "BULK-A", "min_price": 1, "max_price": 2, "our_price": 1},  # already present
				{"sku": "BULK-C", "min_price": 4, "max_price": 8, "our_price": 5},
			]
		)
		self.assertEqual(added, 1)
		self.assertEqual([row.sku for row in doc.rows], ["BULK-A", "BULK-B", "BULK-C"])

	def test_dry_run_validates_and_sets_status(self):
		# dry_run runs the shared guards without touching Amazon.
		doc = self.make_doc()
		result = doc.dry_run()
		self.assertTrue(result["dry_run"])
		self.assertEqual(result["sku_count"], 2)
		self.assertEqual(frappe.db.get_value("Amazon Bulk Price Push", doc.name, "status"), "Validated")

	def test_dry_run_blocks_bad_band(self):
		doc = self.make_doc([{"sku": "BAD", "min_price": 20.0, "max_price": 10.0, "our_price": 15.0}])
		with self.assertRaises(frappe.ValidationError):
			doc.dry_run()

	def test_push_live_submits_and_records_result(self):
		doc = self.make_doc()
		fake_result = {
			"feed_id": "FEED-1",
			"log_name": "APSL-0001",
			"sku_count": 2,
			"skus": ["BULK-A", "BULK-B"],
		}
		# Patch the submit seam and snapshot capture so nothing leaves the test.
		with (
			patch.object(manual_price_push, "submit_feed", return_value=fake_result) as submit,
			patch.object(frappe, "get_single"),
			patch(
				"navgold_custom.navgold_custom.doctype.amazon_pricing_snapshot.amazon_pricing_snapshot.capture_snapshot_now",
				return_value={"name": "PSNAP-TEST"},
			) as capture,
		):
			result = doc.push_live()

		submit.assert_called_once()
		capture.assert_called_once()  # capture_snapshot_before_push defaults to 1
		self.assertEqual(result["feed_id"], "FEED-1")
		saved = frappe.db.get_value(
			"Amazon Bulk Price Push", doc.name, ["status", "submission_log", "snapshot_taken"], as_dict=True
		)
		self.assertEqual(saved.status, "Pushed")
		self.assertEqual(saved.submission_log, "APSL-0001")
		self.assertEqual(saved.snapshot_taken, "PSNAP-TEST")

	def test_push_live_skips_snapshot_when_unchecked(self):
		doc = self.make_doc()
		doc.db_set("capture_snapshot_before_push", 0)
		fake_result = {"feed_id": "FEED-2", "log_name": "APSL-0002", "sku_count": 2, "skus": []}
		with (
			patch.object(manual_price_push, "submit_feed", return_value=fake_result),
			patch.object(frappe, "get_single"),
			patch(
				"navgold_custom.navgold_custom.doctype.amazon_pricing_snapshot.amazon_pricing_snapshot.capture_snapshot_now"
			) as capture,
		):
			doc.push_live()
		capture.assert_not_called()

	def test_fetch_pricing_requires_an_argument(self):
		with self.assertRaises(frappe.ValidationError):
			fetch_pricing_for_bulk()


class TestBulkPricePushFileImport(IntegrationTestCase):
	def test_load_from_file_populates_grid_from_csv(self):
		csv_content = "sku,min,max,our_price\nBULK-X,8.86,12.0,9.5\n,1,2,1\n"
		doc = frappe.new_doc("Amazon Bulk Price Push")
		doc.insert()

		with patch.object(
			amazon_bulk_price_push,
			"load_updates_from_attachment",
			return_value=manual_price_push.parse_csv_rows(
				[line.split(",") for line in csv_content.strip().splitlines()]
			),
		):
			doc.upload_file = "/files/dummy.csv"
			result = doc.load_from_file()

		self.assertEqual(result["loaded"], 1)  # blank-sku row dropped
		self.assertEqual([row.sku for row in doc.rows], ["BULK-X"])
		self.assertEqual(doc.rows[0].price_source, "File")
