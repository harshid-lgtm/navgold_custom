# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.data import cint, flt, get_datetime, now_datetime


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data, summary = get_data_and_summary(filters)
	return columns, data, None, None, summary


def get_columns():
	return [
		{
			"label": _("Proxy SKU"),
			"fieldname": "proxy_sku",
			"fieldtype": "Link",
			"options": "Proxy SKU",
			"width": 200,
		},
		{"label": _("Item"), "fieldname": "item", "fieldtype": "Link", "options": "Item", "width": 160},
		{"label": _("UOM"), "fieldname": "uom", "fieldtype": "Link", "options": "UOM", "width": 100},
		{"label": _("ASIN"), "fieldname": "asin", "fieldtype": "Data", "width": 130},
		{
			"label": _("AFN Stock (Fulfillable)"),
			"fieldname": "afn_stock",
			"fieldtype": "Float",
			"width": 180,
		},
		{"label": _("In Stock"), "fieldname": "in_stock", "fieldtype": "Check", "width": 90},
	]


def get_data_and_summary(filters):
	only_in_stock = cint(filters.get("only_in_stock"))

	proxy_rows = frappe.get_all(
		"Proxy SKU",
		filters={"is_fba": 1, "is_active": 1},
		fields=["name as proxy_sku", "item", "uom", "asin", "afn_stock"],
		order_by="afn_stock desc, name asc",
		limit=0,
	)

	rows = []
	in_stock_count = 0
	total_units = 0.0
	for proxy_row in proxy_rows:
		afn_stock = flt(proxy_row.get("afn_stock"), 4)
		is_in_stock = afn_stock > 0
		if is_in_stock:
			in_stock_count += 1
			total_units = flt(total_units + afn_stock, 4)
		if only_in_stock and not is_in_stock:
			continue
		proxy_row["in_stock"] = 1 if is_in_stock else 0
		rows.append(proxy_row)

	settings = frappe.get_cached_doc("Navgold Settings")
	last_synced_at = settings.get("last_afn_stock_synced_at")
	sync_enabled = cint(settings.get("enable_afn_stock_sync"))

	minutes_since_last_sync = None
	if last_synced_at:
		minutes_since_last_sync = cint((now_datetime() - get_datetime(last_synced_at)).total_seconds() / 60)

	# Stale once the 30-minute scheduled fetch has missed roughly two windows.
	stale = minutes_since_last_sync is None or minutes_since_last_sync > 70

	summary = [
		{
			"label": _("Minutes Since Last FBA Stock Sync"),
			"value": minutes_since_last_sync if minutes_since_last_sync is not None else "-",
			"indicator": "Red" if (sync_enabled and stale) else "Green",
		},
		{
			"label": _("AFN Stock Sync"),
			"value": _("Enabled") if sync_enabled else _("Disabled"),
			"indicator": "Green" if sync_enabled else "Gray",
		},
		{"label": _("FBA SKUs"), "value": len(proxy_rows), "indicator": "Blue"},
		{
			"label": _("SKUs In Stock"),
			"value": in_stock_count,
			"indicator": "Green" if in_stock_count else "Gray",
		},
		{"label": _("Total Fulfillable Units"), "value": flt(total_units, 2), "indicator": "Blue"},
	]
	return rows, summary
