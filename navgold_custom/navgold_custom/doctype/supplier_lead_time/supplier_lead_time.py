# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class SupplierLeadTime(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		day: DF.Literal["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
		lead_time_after_12: DF.Int
		lead_time_before_12: DF.Int
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
	# end: auto-generated types

	pass
