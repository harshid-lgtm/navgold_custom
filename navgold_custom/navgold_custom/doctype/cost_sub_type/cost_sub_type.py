# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.data import flt


class CostSubType(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		cost_type: DF.Link
		default_value: DF.Float
		source: DF.Literal[
			"",
			"Supplier-Sync",
			"PO-Sync",
			"FBA-Transfer",
			"Amazon-Sync",
			"Item-Tax-Sync",
			"Shipping-Sync",
		]
		sub_type_name: DF.Data
	# end: auto-generated types

	def validate(self):
		self.validate_fed_cost_has_no_default()

	def on_update(self):
		# source/default_value feed the cached policy map in price_calculator; without this clear an
		# edit here would leave strategies pricing against the stale map for up to the redis_cache TTL.
		# Imported function-local to keep this controller out of the amazon_pricing import chain at boot.
		from navgold_custom.navgold_custom.amazon_pricing.price_calculator import (
			get_cost_sub_type_policy_map,
		)

		get_cost_sub_type_policy_map.clear_cache()

	def validate_fed_cost_has_no_default(self):
		# A fed cost derives its value from the feed, so a stray default_value would be misleading/dangerous.
		if self.source and flt(self.default_value):
			frappe.throw(
				_("Default Value must be 0 for a fed cost. Sub Type '{0}' is fed by '{1}'.").format(
					self.name, self.source
				)
			)
