# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe

# Merchant shipping sub-types whose cost is now resolved from the weight-based tier table.
SHIPPING_SUB_TYPES = ["Fulfillment Cost-Domestic Regular", "Fulfillment Cost-Amazon Shipping"]


def execute():
	# Shipping cost lines on the cost-set templates were seeded as Manual, so newly generated
	# pricing never picked up the weight-based tier. Point every shipping line at Shipping-Sync so
	# the template applicator copies a self-syncing source onto new strategies.
	template_shipping_lines = frappe.get_all(
		"Amazon Cost Set Template Item",
		filters={"cost_sub_type": ["in", SHIPPING_SUB_TYPES], "source": ["!=", "Shipping-Sync"]},
		pluck="name",
	)
	for name in template_shipping_lines:
		frappe.db.set_value("Amazon Cost Set Template Item", name, "source", "Shipping-Sync")
