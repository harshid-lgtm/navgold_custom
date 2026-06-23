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
