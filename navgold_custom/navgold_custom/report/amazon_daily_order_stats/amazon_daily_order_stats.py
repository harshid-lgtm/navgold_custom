# Copyright (c) 2026, Navgold and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, flt, today


def execute(filters=None):
	filters = frappe._dict(filters or {})
	to_date = filters.get("to_date") or today()
	from_date = filters.get("from_date") or add_days(to_date, -30)

	rows = frappe.get_all(
		"Amazon Daily Order Stat",
		filters={"stat_date": ["between", [from_date, to_date]]},
		fields=[
			"stat_date",
			"total_orders",
			"shipped_orders",
			"cancelled_orders",
			"gross_amount",
			"currency",
		],
		order_by="stat_date asc",
	)

	columns = get_columns()
	chart = get_chart(rows)
	report_summary = get_report_summary(rows)
	return columns, rows, None, chart, report_summary


def get_columns():
	return [
		{"fieldname": "stat_date", "label": _("Date"), "fieldtype": "Date", "width": 110},
		{"fieldname": "total_orders", "label": _("Total Orders"), "fieldtype": "Int", "width": 120},
		{"fieldname": "shipped_orders", "label": _("Shipped"), "fieldtype": "Int", "width": 100},
		{"fieldname": "cancelled_orders", "label": _("Cancelled"), "fieldtype": "Int", "width": 110},
		{
			"fieldname": "gross_amount",
			"label": _("Gross Amount"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 150,
		},
		{
			"fieldname": "currency",
			"label": _("Currency"),
			"fieldtype": "Link",
			"options": "Currency",
			"width": 90,
		},
	]


def get_chart(rows):
	labels = [str(row["stat_date"]) for row in rows]
	return {
		"type": "bar",
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _("Total Orders"), "values": [row["total_orders"] for row in rows]},
				{"name": _("Cancelled"), "values": [row["cancelled_orders"] for row in rows]},
			],
		},
		"colors": ["#449cf0", "#e24c4c"],
		"barOptions": {"stacked": 0},
	}


def get_report_summary(rows):
	total_orders = sum(row["total_orders"] for row in rows)
	total_cancelled = sum(row["cancelled_orders"] for row in rows)
	total_gross = sum(flt(row["gross_amount"]) for row in rows)
	currency = rows[0]["currency"] if rows else "GBP"
	cancel_rate = (total_cancelled / total_orders * 100) if total_orders else 0
	return [
		{"value": total_orders, "label": _("Total Orders"), "datatype": "Int", "indicator": "Blue"},
		{"value": total_cancelled, "label": _("Cancelled"), "datatype": "Int", "indicator": "Red"},
		{
			"value": cancel_rate,
			"label": _("Cancel Rate %"),
			"datatype": "Percent",
			"indicator": "Orange",
		},
		{
			"value": total_gross,
			"label": _("Gross Amount"),
			"datatype": "Currency",
			"currency": currency,
			"indicator": "Green",
		},
	]
