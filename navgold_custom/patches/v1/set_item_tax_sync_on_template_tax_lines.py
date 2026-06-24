# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe


def execute():
	# Tax cost lines on the cost-set templates were seeded as Manual, so newly generated pricing
	# never picked up an item's real VAT rate. Point every VAT line at Item-Tax-Sync so the
	# template applicator copies a self-syncing source onto new strategies.
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
