# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime
from frappe.utils.data import convert_utc_to_system_timezone, get_datetime

from navgold_custom.navgold_custom.amazon_pricing import feed_status_poller
from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_submitter import check_feed_status
from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import SPAPIError

GET_FEEDS_CLIENT_PATH = "navgold_custom.navgold_custom.amazon_pricing.amazon_feed_submitter.get_feeds_client"


def create_submission_log(
	status="Submitted",
	submission_type="Amazon",
	feed_id="FEED-TEST-1",
	submitted_at=None,
):
	log = frappe.new_doc("Amazon Pricing Submission Log")
	log.submission_type = submission_type
	log.status = status
	log.feed_id = feed_id
	log.submitted_at = submitted_at or now_datetime()
	# Nonexistent pricing name: keeps check_feed_status's bulk set_value a harmless no-op.
	log.pricing_records = "NONEXISTENT-PRICING-RECORD"
	log.insert()
	return log


class TestFeedStatusPoller(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.set_single_value("Amazon SP API Settings", "is_active", 1)

	def test_poller_selects_only_eligible_logs(self):
		eligible = create_submission_log()
		wrong_status = create_submission_log(status="Completed")
		wrong_type = create_submission_log(submission_type="Repricer")
		missing_feed_id = create_submission_log(feed_id=None)
		too_old = create_submission_log(submitted_at=add_days(now_datetime(), -8))

		with patch.object(feed_status_poller, "check_feed_status") as mocked_check:
			feed_status_poller.poll_pending_feed_submissions()

		checked_names = [call.args[0] for call in mocked_check.call_args_list]
		self.assertIn(eligible.name, checked_names)
		for ineligible in (wrong_status, wrong_type, missing_feed_id, too_old):
			self.assertNotIn(ineligible.name, checked_names)

	def test_poller_respects_cap_and_polls_oldest_first(self):
		oldest = create_submission_log(submitted_at=add_days(now_datetime(), -6))
		middle = create_submission_log(submitted_at=add_days(now_datetime(), -3))
		newest = create_submission_log(submitted_at=now_datetime())

		with (
			patch.object(feed_status_poller, "POLL_BATCH_SIZE", 2),
			patch.object(feed_status_poller, "check_feed_status") as mocked_check,
		):
			feed_status_poller.poll_pending_feed_submissions()

		checked_names = [call.args[0] for call in mocked_check.call_args_list]
		self.assertEqual(len(checked_names), 2)
		self.assertEqual(checked_names, [oldest.name, middle.name])
		self.assertNotIn(newest.name, checked_names)

	def test_poller_continues_after_single_failure(self):
		failing = create_submission_log(submitted_at=add_days(now_datetime(), -2))
		healthy = create_submission_log()

		def fail_for_first(log_name):
			if log_name == failing.name:
				raise SPAPIError(error="HTTP 500", error_description="boom")

		with patch.object(
			feed_status_poller, "check_feed_status", side_effect=fail_for_first
		) as mocked_check:
			feed_status_poller.poll_pending_feed_submissions()

		checked_names = [call.args[0] for call in mocked_check.call_args_list]
		self.assertIn(failing.name, checked_names)
		self.assertIn(healthy.name, checked_names)

	def test_completed_at_taken_from_amazon_processing_end_time(self):
		log = create_submission_log()
		processing_end_time = "2026-06-01T10:30:00+00:00"
		feeds_client = MagicMock()
		feeds_client.get_feed.return_value = {
			"processingStatus": "DONE",
			"processingEndTime": processing_end_time,
			"resultFeedDocumentId": "RESULT-DOC-1",
		}

		with patch(GET_FEEDS_CLIENT_PATH, return_value=feeds_client):
			result = check_feed_status(log.name)

		self.assertEqual(result["status"], "DONE")

		log.reload()
		self.assertEqual(log.status, "Completed")
		expected_completed_at = convert_utc_to_system_timezone(get_datetime(processing_end_time)).replace(
			tzinfo=None
		)
		self.assertEqual(get_datetime(log.completed_at), expected_completed_at)

	def test_dead_feed_marks_log_as_error(self):
		log = create_submission_log()
		feeds_client = MagicMock()
		feeds_client.get_feed.side_effect = SPAPIError(
			error="HTTP 404", error_description="NotFound: Feed FEED-TEST-1 does not exist"
		)

		with patch(GET_FEEDS_CLIENT_PATH, return_value=feeds_client):
			result = check_feed_status(log.name)

		self.assertEqual(result["status"], "NOT_FOUND")

		log.reload()
		self.assertEqual(log.status, "Error")
		self.assertIn("not found on amazon", log.error_message.lower())
