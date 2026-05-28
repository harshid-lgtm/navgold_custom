# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.utils.data import flt


def update_proxy_sku_transfer_rates(doc, method):
	if doc.stock_entry_type != "FBA Transfer":
		return

	rate_by_item = {}
	for item_row in doc.items:
		if item_row.item_code in rate_by_item:
			continue
		rate_by_item[item_row.item_code] = flt(item_row.basic_rate)

	for item_code, rate in rate_by_item.items():
		frappe.db.set_value(
			"Proxy SKU",
			{"item": item_code, "is_fba": 1},
			"last_transfer_rate",
			rate,
			update_modified=False,
		)
