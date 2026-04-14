# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from navgold_custom.navgold_custom.amazon_repository import get_order


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
