# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CostSubType(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		cost_type: DF.Link
		default_value: DF.Float
		is_default: DF.Check
		sub_type_name: DF.Data
	# end: auto-generated types

	def validate(self):
		self.validate_single_default()

	def validate_single_default(self):
		if self.is_default:
			existing = frappe.db.exists(
				"Cost Sub Type", {"cost_type": self.cost_type, "is_default": 1, "name": ("!=", self.name)}
			)
			if existing:
				frappe.throw(
					_(
						"Cost Type '{0}' already has a default sub type. Please unset the current default first."
					).format(self.cost_type)
				)
