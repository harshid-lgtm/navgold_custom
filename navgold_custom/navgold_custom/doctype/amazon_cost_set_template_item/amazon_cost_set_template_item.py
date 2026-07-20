# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AmazonCostSetTemplateItem(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		cost_sub_type: DF.Link | None
		cost_type: DF.Link
		default_value: DF.Float
		is_active: DF.Check
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		rank: DF.Int
	# end: auto-generated types

	pass
