# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.data import cint, flt


def get_shipping_cost(shipping_weight_g) -> float:
	"""Resolve the merchant shipping cost for an effective weight using Navgold Settings tiers."""
	weight = cint(shipping_weight_g)
	if weight <= 0:
		frappe.throw(
			_("Set Shipping Weight (g) on the Proxy SKU before pricing a merchant-fulfilled strategy.")
		)

	settings = frappe.get_cached_doc("Navgold Settings")
	tiers = sorted(settings.shipping_weight_tiers, key=lambda tier: cint(tier.upper_weight_g))
	if not tiers:
		frappe.throw(_("Configure Shipping Weight Tiers in Navgold Settings before pricing shipping."))

	effective_weight = weight + cint(settings.shipping_packaging_buffer_g)

	for tier in tiers:
		if effective_weight <= cint(tier.upper_weight_g):
			return flt(tier.shipping_cost)

	return flt(tiers[-1].shipping_cost)
