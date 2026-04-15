# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AmazonStrategyTemplateItem(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		cost_set_template: DF.Link | None
		max_margin: DF.Percent
		min_margin: DF.Percent
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		repricer_rule: DF.Link | None
		strategy_name: DF.Data
		strategy_type: DF.Literal["FBA", "SFP", "Backorder", "Default"]
	# end: auto-generated types

	pass
