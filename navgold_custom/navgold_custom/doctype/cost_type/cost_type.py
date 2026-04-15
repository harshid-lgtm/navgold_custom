# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CostType(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		cost_type_name: DF.Data
		is_absolute: DF.Check
		is_purchase_cost: DF.Check
		is_relative: DF.Check
		is_vat: DF.Check
	# end: auto-generated types

	def validate(self):
		self.validate_type_flags()

	def validate_type_flags(self):
		flags = [self.is_absolute, self.is_relative, self.is_vat]
		if sum(flags) > 1:
			frappe.throw(_("Only one of 'Is Absolute', 'Is Relative', or 'Is VAT' can be selected"))
		if sum(flags) == 0:
			frappe.throw(_("At least one of 'Is Absolute', 'Is Relative', or 'Is VAT' must be selected"))
