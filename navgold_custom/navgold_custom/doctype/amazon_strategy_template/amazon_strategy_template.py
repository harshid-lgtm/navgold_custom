# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AmazonStrategyTemplate(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from navgold_custom.navgold_custom.doctype.amazon_strategy_template_item.amazon_strategy_template_item import (
			AmazonStrategyTemplateItem,
		)

		description: DF.SmallText | None
		is_default: DF.Check
		strategies: DF.Table[AmazonStrategyTemplateItem]
		template_name: DF.Data
	# end: auto-generated types

	def validate(self):
		self.validate_single_default()
		self.validate_strategy_types()

	def validate_single_default(self):
		if self.is_default:
			existing = frappe.db.exists(
				"Amazon Strategy Template", {"is_default": 1, "name": ("!=", self.name)}
			)
			if existing:
				frappe.throw(
					_(
						"Another strategy template is already set as default. "
						"Please unset the current default first."
					)
				)

	def validate_strategy_types(self):
		strategy_types = [template_item.strategy_type for template_item in self.strategies]
		if len(strategy_types) != len(set(strategy_types)):
			frappe.throw(_("Each strategy type (FBA, SFP, Backorder, Default) can only appear once"))

	@frappe.whitelist()
	def apply_to_pricing(self, pricing_name: str):
		pricing = frappe.get_cached_doc("Amazon Product Pricing", pricing_name)

		strategy_map = {}
		for template_strategy in self.strategies:
			strategy = frappe.new_doc("Amazon Product Strategy")
			strategy.strategy_name = template_strategy.strategy_name
			strategy.proxy_sku = pricing.proxy_sku
			strategy.min_margin = template_strategy.min_margin
			strategy.max_margin = template_strategy.max_margin
			strategy.repricer_rule = template_strategy.repricer_rule
			strategy.is_active = 1

			if template_strategy.cost_set_template:
				cost_template = frappe.get_cached_doc(
					"Amazon Cost Set Template", template_strategy.cost_set_template
				)
				for cost_item in cost_template.costs:
					strategy.append(
						"costs",
						{
							"cost_type": cost_item.cost_type,
							"cost_sub_type": cost_item.cost_sub_type,
							"value": cost_item.default_value,
							"is_active": cost_item.is_active,
							"rank": cost_item.rank,
							"source": "Manual",
						},
					)

			strategy.insert()
			strategy_map[template_strategy.strategy_type] = strategy.name

		if "FBA" in strategy_map:
			pricing.fba_strategy = strategy_map["FBA"]
		if "SFP" in strategy_map:
			pricing.sfp_strategy = strategy_map["SFP"]
		if "Backorder" in strategy_map:
			pricing.backorder_strategy = strategy_map["Backorder"]
		if "Default" in strategy_map:
			pricing.default_strategy = strategy_map["Default"]

		pricing.save()

		return {"pricing": pricing_name, "strategies_created": len(strategy_map)}
