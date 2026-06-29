# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.report_attachments import save_last_report_attachment

FIELD = "last_listings_report"


class TestReportAttachments(IntegrationTestCase):
	def remaining_files(self):
		return frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": "Navgold Settings",
				"attached_to_name": "Navgold Settings",
				"attached_to_field": FIELD,
			},
			pluck="name",
		)

	def test_replaces_previous_so_only_latest_is_kept(self):
		save_last_report_attachment(FIELD, "amazon_all_listings.tsv", "sku\tprice\nA\t1\n")
		save_last_report_attachment(FIELD, "amazon_all_listings.tsv", "sku\tprice\nB\t2\n")
		latest_url = save_last_report_attachment(FIELD, "amazon_all_listings.tsv", "sku\tprice\nC\t3\n")

		remaining = self.remaining_files()
		self.assertEqual(len(remaining), 1, "Only the most recent fetch should remain attached")

		self.assertEqual(frappe.db.get_single_value("Navgold Settings", FIELD), latest_url)

		current = frappe.get_doc("File", remaining[0])
		self.assertIn("C\t3", current.get_content())

	def test_reattaching_identical_content_does_not_error(self):
		# Re-fetching a report whose bytes are unchanged must still replace cleanly and leave
		# exactly one file — the content-hash filename path is the likely place to regress.
		content = "sku\tprice\nA\t1\n"
		save_last_report_attachment(FIELD, "amazon_all_listings.tsv", content)
		save_last_report_attachment(FIELD, "amazon_all_listings.tsv", content)

		remaining = self.remaining_files()
		self.assertEqual(len(remaining), 1)
		self.assertEqual(frappe.get_doc("File", remaining[0]).get_content(), content)
