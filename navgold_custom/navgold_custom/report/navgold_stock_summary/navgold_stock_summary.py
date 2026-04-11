# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Sum


def execute(filters: dict | None = None):
	"""Return columns and data for the report.

	This is the main entry point for the report. It accepts the filters as a
	dictionary and should return columns and data. It is called by the framework
	every time the report is refreshed or a filter is updated.
	"""
	columns = get_columns()
	data = get_data()

	return columns, data


def get_columns() -> list[dict]:
	"""Return columns for the report.

	One field definition per column, just like a DocType field definition.
	"""
	return [
		{
			"label": _("Item Code"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 150,
		},
		{
			"label": _("Free Qty"),
			"fieldname": "free_qty",
			"fieldtype": "Int",
			"width": 150,
		},
		{
			"label": _("Actual Qty"),
			"fieldname": "actual_qty",
			"fieldtype": "Int",
			"width": 150,
		},
		{
			"label": _("Projected Qty"),
			"fieldname": "projected_qty",
			"fieldtype": "Int",
			"width": 150,
		},
		{
			"label": _("Reserved Qty"),
			"fieldname": "reserved_qty",
			"fieldtype": "Int",
			"width": 150,
		},
		{
			"label": _("Requested Qty"),
			"fieldname": "requested_qty",
			"fieldtype": "Int",
			"width": 150,
		},
		{
			"label": _("Ordered Qty"),
			"fieldname": "ordered_qty",
			"fieldtype": "Int",
			"width": 150,
		},
	]


def get_data() -> list[list]:
	"""Return data for the report.

	The report data is a list of rows, with each row being a list of cell values.
	"""
	item = DocType("Item")
	bin = DocType("Bin")
	warehouse = DocType("Warehouse")
	item_summary = (
		(
			frappe.qb.from_(item)
			.left_join(bin)
			.on(item.name == bin.item_code)
			.left_join(warehouse)
			.on(bin.warehouse == warehouse.name)
		)
		.select(
			item.name.as_("item_code"),
			(Sum(bin.actual_qty) - Sum(bin.reserved_qty)).as_("free_qty"),
			Sum(bin.actual_qty),
			Sum(bin.projected_qty),
			Sum(bin.reserved_qty),
			Sum(bin.indented_qty).as_("requested_qty"),
			Sum(bin.ordered_qty),
		)
		.where((item.disabled == 0) & (warehouse.is_rejected_warehouse == 0))
		.groupby(item.name)
	)
	return item_summary.run()
