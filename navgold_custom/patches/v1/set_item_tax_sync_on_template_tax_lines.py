# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe


def execute():
	# The `source` field on this child table was removed in the cost-source remodel; nothing to do then.
	if not frappe.get_meta("Amazon Cost Set Template Item").get_field("source"):
		return

	# Repoint template VAT lines from Manual to Item-Tax-Sync so new strategies pick up each item's real VAT.
	vat_cost_types = frappe.get_all("Cost Type", filters={"calculation_type": "VAT"}, pluck="name")
	if not vat_cost_types:
		return

	template_tax_lines = frappe.get_all(
		"Amazon Cost Set Template Item",
		filters={"cost_type": ["in", vat_cost_types], "source": ["!=", "Item-Tax-Sync"]},
		pluck="name",
	)
	for name in template_tax_lines:
		frappe.db.set_value("Amazon Cost Set Template Item", name, "source", "Item-Tax-Sync")
