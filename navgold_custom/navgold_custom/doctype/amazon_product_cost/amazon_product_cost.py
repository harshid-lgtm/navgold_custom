# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AmazonProductCost(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		cost_sub_type: DF.Link | None
		cost_type: DF.Link
		is_active: DF.Check
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		rank: DF.Int
		source: DF.Literal[
			"Manual",
			"Supplier-Sync",
			"PO-Sync",
			"Auto-Sync",
			"FBA-Transfer",
			"Amazon-Sync",
			"Item-Tax-Sync",
			"Shipping-Sync",
		]
		value: DF.Float
	# end: auto-generated types

	pass
