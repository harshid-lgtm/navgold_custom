# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe

from navgold_custom.navgold_custom.amazon_pricing.shipping_weight import get_shipping_weight_g


def execute():
	# The validate() auto-fill only fires on save, so existing Proxy SKUs have no shipping weight.
	# Seed it from the Item weight for SKUs with an unset weight and a derivable value. SKUs whose
	# Item weight is missing/unusable are left at 0 — the Shipping-Sync resolver surfaces those.
	proxy_skus = frappe.get_all(
		"Proxy SKU",
		filters={"item": ["is", "set"], "shipping_weight_g": ["in", [0, None]]},
		fields=["name", "item", "uom"],
	)

	updated = 0
	for proxy_sku in proxy_skus:
		weight_g = get_shipping_weight_g(proxy_sku.item, proxy_sku.uom)
		if not weight_g:
			continue
		frappe.db.set_value(
			"Proxy SKU", proxy_sku.name, "shipping_weight_g", round(weight_g), update_modified=False
		)
		updated += 1
		if updated % 500 == 0:
			frappe.db.commit()  # nosemgrep: frappe-manual-commit -- long-running data patch, batch commit
