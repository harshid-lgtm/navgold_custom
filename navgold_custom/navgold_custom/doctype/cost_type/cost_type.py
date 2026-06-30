# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CostType(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		calculation_type: DF.Literal["Absolute", "Relative", "VAT"]
		cost_type_name: DF.Data
		is_purchase_cost: DF.Check
	# end: auto-generated types

	# Price calculation reads cost types through a cached map; refresh it on change.
	def on_update(self):
		clear_cost_type_map_cache()

	def on_trash(self):
		clear_cost_type_map_cache()


def clear_cost_type_map_cache():
	from navgold_custom.navgold_custom.amazon_pricing.price_calculator import get_cost_type_map

	get_cost_type_map.clear_cache()
