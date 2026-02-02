# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AmazonCostSetTemplate(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from navgold_custom.navgold_custom.doctype.amazon_cost_set_template_item.amazon_cost_set_template_item import (
			AmazonCostSetTemplateItem,
		)

		costs: DF.Table[AmazonCostSetTemplateItem]
		description: DF.SmallText | None
		is_default: DF.Check
		template_name: DF.Data
	# end: auto-generated types

	def validate(self):
		self.validate_single_default()

	def validate_single_default(self):
		"""Ensure only one default template"""
		if self.is_default:
			existing = frappe.db.exists(
				"Amazon Cost Set Template", {"is_default": 1, "name": ("!=", self.name)}
			)
			if existing:
				frappe.throw(
					"Another cost set template is already set as default. "
					"Please unset the current default first."
				)

	@frappe.whitelist()
	def apply_to_strategy(self, strategy_name: str):
		"""Apply this template's costs to an Amazon Product Strategy"""
		strategy = frappe.get_doc("Amazon Product Strategy", strategy_name)

		# Clear existing costs
		strategy.costs = []

		# Add costs from template
		for template_cost in self.costs:
			strategy.append(
				"costs",
				{
					"cost_type": template_cost.cost_type,
					"cost_sub_type": template_cost.cost_sub_type,
					"value": template_cost.default_value,
					"is_active": template_cost.is_active,
					"rank": template_cost.rank,
					"source": "Manual",
				},
			)

		strategy.save()
		return {"success": True, "strategy": strategy_name}
