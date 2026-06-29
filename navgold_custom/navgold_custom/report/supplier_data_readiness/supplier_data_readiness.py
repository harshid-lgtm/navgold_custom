# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder import Case, DocType
from frappe.query_builder.functions import Count, Max, Sum
from frappe.utils.data import cint, date_diff, now_datetime


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{
			"label": _("Supplier"),
			"fieldname": "supplier",
			"fieldtype": "Link",
			"options": "Supplier",
			"width": 220,
		},
		{"label": _("Items"), "fieldname": "item_count", "fieldtype": "Int", "width": 90},
		{"label": _("Items In Stock"), "fieldname": "in_stock_count", "fieldtype": "Int", "width": 110},
		{"label": _("Last Updated"), "fieldname": "last_updated", "fieldtype": "Datetime", "width": 160},
		{"label": _("Days Stale"), "fieldname": "days_stale", "fieldtype": "Int", "width": 90},
		{"label": _("Stale"), "fieldname": "is_stale", "fieldtype": "Check", "width": 70},
	]


def get_data(filters):
	staleness_days = cint(filters.get("staleness_days")) or 7
	supplier_price = DocType("Supplier Item Price and Stock")
	supplier = DocType("Supplier")

	rows = (
		frappe.qb.from_(supplier_price)
		.join(supplier)
		.on(supplier_price.supplier == supplier.name)
		.select(
			supplier_price.supplier,
			Count(supplier_price.name).as_("item_count"),
			Sum(Case().when(supplier_price.stock_qty > 0, 1).else_(0)).as_("in_stock_count"),
			Max(supplier_price.modified).as_("last_updated"),
		)
		.where(supplier.disabled == 0)
		.groupby(supplier_price.supplier)
	).run(as_dict=True)

	now = now_datetime()
	for row in rows:
		row["days_stale"] = date_diff(now, row.last_updated)
		row["is_stale"] = 1 if row["days_stale"] > staleness_days else 0
		row["in_stock_count"] = cint(row.in_stock_count)

	rows.sort(key=lambda row: row["days_stale"], reverse=True)
	return rows
