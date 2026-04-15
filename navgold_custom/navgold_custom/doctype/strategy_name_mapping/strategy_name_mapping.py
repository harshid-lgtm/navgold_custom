# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class StrategyNameMapping(Document):
	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		pricing_field: DF.Literal["FBA Strategy", "SFP Strategy", "Backorder Strategy", "Default Strategy"]
		strategy_name: DF.Data
