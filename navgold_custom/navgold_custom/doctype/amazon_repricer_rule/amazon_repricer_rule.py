# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AmazonRepricerRule(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		description: DF.SmallText | None
		repricer_rule_id: DF.Data | None
		rule_name: DF.Data
	# end: auto-generated types

	pass
