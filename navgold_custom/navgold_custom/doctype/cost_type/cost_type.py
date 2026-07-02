# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.data import flt
from frappe.utils.nestedset import NestedSet


class CostType(NestedSet):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		calculation_type: DF.Literal["Absolute", "Relative", "VAT"]
		cost_type_name: DF.Data
		default_value: DF.Float
		is_group: DF.Check
		is_purchase_cost: DF.Check
		lft: DF.Int
		old_parent: DF.Link | None
		parent_cost_type: DF.Link | None
		rgt: DF.Int
		source: DF.Literal[
			"",
			"Supplier-Sync",
			"PO-Sync",
			"FBA-Transfer",
			"Amazon-Sync",
			"Item-Tax-Sync",
			"Shipping-Sync",
		]
	# end: auto-generated types

	nsm_parent_field = "parent_cost_type"

	def autoname(self):
		# A node with no parent can only be a group, so it keeps the flat cost-type name; only a parented
		# leaf gets the "{parent}-{name}" shape (identical to the old Cost Sub Type name).
		if self.is_group or not self.parent_cost_type:
			self.name = self.cost_type_name
		else:
			self.name = f"{self.parent_cost_type}-{self.cost_type_name}"

	def validate(self):
		if self.is_group:
			self.source = ""
			self.default_value = 0
		self.validate_fed_cost_has_no_default()

	def validate_fed_cost_has_no_default(self):
		# A fed cost derives its value from the feed, so a stray default_value would be misleading/dangerous.
		if self.source and flt(self.default_value):
			frappe.throw(
				_("Default Value must be 0 for a fed cost. '{0}' is fed by '{1}'.").format(
					self.name, self.source
				)
			)

	def on_update(self):
		super().on_update()
		self.clear_pricing_caches()

	def on_trash(self):
		super().on_trash()
		self.clear_pricing_caches()

	def clear_pricing_caches(self):
		# Group calc rules and leaf value policy both feed cached maps in price_calculator; a change here
		# must refresh both or strategies keep pricing against a stale map until the redis_cache TTL.
		# Imported function-local to keep this controller out of the amazon_pricing import chain at boot.
		from navgold_custom.navgold_custom.amazon_pricing.price_calculator import (
			get_cost_sub_type_map,
			get_cost_type_map,
		)

		get_cost_type_map.clear_cache()
		get_cost_sub_type_map.clear_cache()
