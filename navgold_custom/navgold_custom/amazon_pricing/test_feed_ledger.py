# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing import feed_ledger
from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import Feeds

NAV_FEED = {
	"feedId": "TEST-FEED-NAV",
	"feedType": "JSON_LISTINGS_FEED",
	"processingStatus": "DONE",
	"createdTime": "2026-06-23T05:00:00+00:00",
	"processingEndTime": "2026-06-23T05:05:00+00:00",
	"resultFeedDocumentId": "TEST-DOC-NAV",
}
EXT_FEED = {
	"feedId": "TEST-FEED-EXT",
	"feedType": "JSON_LISTINGS_FEED",
	"processingStatus": "DONE",
	"createdTime": "2026-06-23T05:10:00+00:00",
	"processingEndTime": "2026-06-23T05:14:00+00:00",
	"resultFeedDocumentId": "TEST-DOC-EXT",
}
FEED_IDS = [NAV_FEED["feedId"], EXT_FEED["feedId"]]


class TestFeedLedgerSync(IntegrationTestCase):
	def setUp(self):
		# run_sync commits, so it escapes the per-test rollback — purge our fixed test ids
		# explicitly so a re-run never matches a stale log with the same feed id.
		self.cleanup()
		self.log = frappe.get_doc(
			{
				"doctype": "Amazon Pricing Submission Log",
				"submission_type": "Amazon",
				"status": "Completed",
				"feed_id": NAV_FEED["feedId"],
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		self.cleanup()

	def cleanup(self):
		frappe.db.delete("Amazon Feed Ledger", {"feed_id": ["in", FEED_IDS]})
		frappe.db.delete("Amazon Pricing Submission Log", {"feed_id": ["in", FEED_IDS]})
		frappe.db.commit()

	def patched_sync(self):
		fake_client = MagicMock()
		fake_client.get_feeds.return_value = {"feeds": [NAV_FEED, EXT_FEED], "nextToken": None}
		fake_client.get_feed_document.return_value = {"url": "https://x", "compressionAlgorithm": None}
		summary = json.dumps(
			{"summary": {"messagesProcessed": 10, "messagesAccepted": 8, "messagesInvalid": 2}}
		).encode()
		with (
			patch.object(feed_ledger, "get_feeds_client", return_value=fake_client),
			patch.object(feed_ledger, "download_binary", return_value=summary),
		):
			return feed_ledger.run_sync()

	def test_source_matching_and_counts(self):
		result = self.patched_sync()
		self.assertEqual(result["inserted"], 2)
		self.assertEqual(result["results_fetched"], 2)

		nav = frappe.get_doc("Amazon Feed Ledger", NAV_FEED["feedId"])
		self.assertEqual(nav.source, "Navgold")
		self.assertEqual(nav.submission_log, self.log.name)
		self.assertEqual(nav.messages_accepted, 8)
		self.assertEqual(nav.messages_invalid, 2)
		self.assertTrue(nav.result_fetched)

		ext = frappe.get_doc("Amazon Feed Ledger", EXT_FEED["feedId"])
		self.assertEqual(ext.source, "External")
		self.assertFalse(ext.submission_log)
		self.assertEqual(ext.messages_accepted, 8)

	def test_resync_is_idempotent(self):
		self.patched_sync()
		result = self.patched_sync()
		# Second run updates in place; no duplicate ledger rows for the same feed id.
		self.assertEqual(result["inserted"], 0)
		self.assertEqual(len(frappe.get_all("Amazon Feed Ledger", filters={"feed_id": ["in", FEED_IDS]})), 2)


class TestGetFeedsParams(IntegrationTestCase):
	def build_feeds_client(self):
		return Feeds(client_id="x", client_secret="y", refresh_token="z", country_code="GB")

	def test_array_params_are_comma_separated(self):
		client = self.build_feeds_client()
		captured = {}
		with patch.object(client, "make_request", side_effect=lambda **kwargs: captured.update(kwargs) or {}):
			client.get_feeds(
				feed_types=["JSON_LISTINGS_FEED"], page_size=50, created_since="2026-01-01T00:00:00Z"
			)
		self.assertEqual(captured["params"]["feedTypes"], "JSON_LISTINGS_FEED")
		self.assertEqual(captured["params"]["marketplaceIds"], "A1F83G8C2ARO7P")

	def test_next_token_sent_alone(self):
		client = self.build_feeds_client()
		captured = {}
		with patch.object(client, "make_request", side_effect=lambda **kwargs: captured.update(kwargs) or {}):
			client.get_feeds(next_token="TOKEN-123")
		self.assertEqual(captured["params"], {"nextToken": "TOKEN-123"})
