# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe


def execute():
	# fetch_from only populates on save, so the new `item` link (sourced from proxy_sku.item)
	# is empty on existing rows. Seed it so the Item dashboard's "Amazon Linked" connections
	# resolve counts for records created before this field existed.
	for doctype in ("Amazon Product Pricing", "Amazon Product Strategy"):
		rows = frappe.get_all(
			doctype,
			filters={"item": ["is", "not set"], "proxy_sku": ["is", "set"]},
			fields=["name", "proxy_sku"],
		)

		proxy_sku_items = dict(
			frappe.get_all(
				"Proxy SKU",
				filters={"name": ["in", [row.proxy_sku for row in rows]], "item": ["is", "set"]},
				fields=["name", "item"],
				as_list=True,
			)
		)

		updated = 0
		for row in rows:
			item = proxy_sku_items.get(row.proxy_sku)
			if not item:
				continue
			frappe.db.set_value(doctype, row.name, "item", item, update_modified=False)
			updated += 1
			if updated % 500 == 0:
				frappe.db.commit()  # nosemgrep: frappe-manual-commit -- long-running data patch, batch commit
