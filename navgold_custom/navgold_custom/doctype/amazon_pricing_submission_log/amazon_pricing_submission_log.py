# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AmazonPricingSubmissionLog(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		amazon_status: DF.Data | None
		completed_at: DF.Datetime | None
		error_message: DF.LongText | None
		feed_content: DF.Code | None
		feed_document_id: DF.Data | None
		feed_id: DF.Data | None
		pricing_records: DF.LongText | None
		result_document_id: DF.Data | None
		status: DF.Literal["Pending", "Submitted", "Completed", "Error"]
		submission_type: DF.Literal["Amazon", "Repricer"]
		submitted_at: DF.Datetime | None
	# end: auto-generated types

	@frappe.whitelist()
	def check_status(self):
		"""Check feed status from Amazon"""
		from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_submitter import check_feed_status

		if self.submission_type != "Amazon":
			frappe.throw(_("Can only check status for Amazon submissions"))

		result = check_feed_status(self.name)
		self.reload()
		return result

	@frappe.whitelist()
	def retry_submission(self):
		"""Retry a failed submission"""
		from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_submitter import submit_pricing

		if self.status != "Error":
			frappe.throw(_("Can only retry failed submissions"))

		pricing_names = [p.strip() for p in self.pricing_records.split("\n") if p.strip()]
		return submit_pricing(pricing_names)
