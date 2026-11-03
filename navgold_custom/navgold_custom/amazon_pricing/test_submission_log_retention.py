# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime

from navgold_custom.navgold_custom.amazon_pricing.submission_log_retention import (
	purge_old_submission_payloads,
)


class TestSubmissionLogRetention(IntegrationTestCase):
	def make_log(self, *, status, days_old, feed_content="feed", response_content="response"):
		log = frappe.new_doc("Amazon Pricing Submission Log")
		log.submission_type = "Amazon"
		log.status = status
		log.feed_content = feed_content
		log.response_content = response_content
		log.pricing_records = "PRICE-1\nPRICE-2"
		log.blocked_records = "SKU-1: blocked"
		log.supplier_swap_notes = "SKU-2: supplier fallback"
		log.insert(ignore_permissions=True)
		frappe.db.set_value(
			"Amazon Pricing Submission Log",
			log.name,
			{
				"creation": add_days(now_datetime(), -days_old),
				"modified": add_days(now_datetime(), -days_old),
			},
			update_modified=False,
		)
		return log.name

	def test_old_completed_payloads_are_cleared_but_audit_row_remains(self):
		name = self.make_log(status="Completed", days_old=120)

		purged = purge_old_submission_payloads()

		self.assertEqual(purged, 1)
		row = frappe.db.get_value(
			"Amazon Pricing Submission Log",
			name,
			[
				"status",
				"feed_content",
				"response_content",
				"pricing_records",
				"blocked_records",
				"supplier_swap_notes",
			],
			as_dict=True,
		)
		self.assertEqual(row.status, "Completed")
		self.assertFalse(row.feed_content)
		self.assertFalse(row.response_content)
		self.assertFalse(row.pricing_records)
		self.assertFalse(row.blocked_records)
		self.assertFalse(row.supplier_swap_notes)

	def test_recent_terminal_log_is_kept(self):
		name = self.make_log(status="Completed", days_old=29)

		purge_old_submission_payloads()

		row = frappe.db.get_value(
			"Amazon Pricing Submission Log",
			name,
			[
				"feed_content",
				"response_content",
				"pricing_records",
				"blocked_records",
				"supplier_swap_notes",
			],
			as_dict=True,
		)
		self.assertEqual(row.feed_content, "feed")
		self.assertEqual(row.response_content, "response")
		self.assertEqual(row.pricing_records, "PRICE-1\nPRICE-2")
		self.assertEqual(row.blocked_records, "SKU-1: blocked")
		self.assertEqual(row.supplier_swap_notes, "SKU-2: supplier fallback")

	def test_pending_and_submitted_logs_are_never_touched(self):
		for status in ("Pending", "Submitted"):
			name = self.make_log(status=status, days_old=180)

			purge_old_submission_payloads()

			row = frappe.db.get_value(
				"Amazon Pricing Submission Log",
				name,
				["feed_content", "response_content"],
				as_dict=True,
			)
			self.assertEqual(row.feed_content, "feed")
			self.assertEqual(row.response_content, "response")
