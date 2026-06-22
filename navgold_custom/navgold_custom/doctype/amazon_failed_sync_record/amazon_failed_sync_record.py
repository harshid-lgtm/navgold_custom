# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import time

import frappe
from frappe import _
from frappe.model.document import Document

from navgold_custom.navgold_custom.amazon_repository import get_order

# Amazon's getOrders endpoint throttles aggressively; pace sequential retries.
RETRY_THROTTLE_SECONDS = 2


class AmazonFailedSyncRecord(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		amazon_order_amount: DF.Currency
		amazon_order_date: DF.Date | None
		amazon_order_id: DF.Data
		grand_total: DF.Currency
		payload: DF.JSON | None
		posting_date: DF.Date | None
		remarks: DF.SmallText | None
	# end: auto-generated types

	@frappe.whitelist()
	def retry_fetching(self):
		if not self.amazon_order_id:
			return 0

		existing_so = frappe.db.get_value(
			"Sales Order",
			{"amazon_order_id": self.amazon_order_id, "docstatus": 1},
			"name",
		)
		if existing_so:
			self.status = "Resolved"
			self.remarks = f"Sales Order {existing_so} already exists"
			self.save()
			return [existing_so]

		try:
			result = get_order(amazon_order_ids=self.amazon_order_id)
			if result:
				self.status = "Resolved"
				self.save()
			return result
		except Exception:
			self.remarks = frappe.get_traceback()
			self.save()
			return 0


@frappe.whitelist()
def bulk_retry_failed_syncs(names) -> dict:
	names = frappe.parse_json(names)
	if not names:
		frappe.throw(_("No records selected"))

	failed_names = frappe.get_all(
		"Amazon Failed Sync Record",
		filters={"name": ("in", names), "status": "Failed"},
		pluck="name",
	)

	invalid_count = len(set(names)) - len(failed_names)
	if invalid_count:
		frappe.throw(_("{0} selected record(s) are not in Failed status").format(invalid_count))

	frappe.enqueue(
		job_id="navgold.bulk_retry_failed_syncs",
		deduplicate=True,
		method=run_bulk_retry,
		record_names=failed_names,
		queue="long",
		timeout=6000,
	)

	return {"queued": len(failed_names)}


def run_bulk_retry(record_names: list):
	for index, record_name in enumerate(record_names):
		try:
			# retry_fetching is a document method; a per-doc load is required to reuse it.
			record = frappe.get_doc("Amazon Failed Sync Record", record_name)
			record.retry_fetching()
		except Exception:
			frappe.log_error(
				f"Bulk retry failed for {record_name}: {frappe.get_traceback()}",
				"Amazon Failed Sync Bulk Retry",
			)

		# Commit per record so Sales Orders already created on Amazon's side survive a
		# mid-batch worker restart.
		frappe.db.commit()

		if index < len(record_names) - 1:
			time.sleep(RETRY_THROTTLE_SECONDS)
