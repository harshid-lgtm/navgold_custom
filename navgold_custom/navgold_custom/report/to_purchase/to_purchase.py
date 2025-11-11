# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count
from pypika.functions import Sum
from pypika.terms import Case


def execute(filters: dict | None = None):
	"""Return columns and data for the report.

	This is the main entry point for the report. It accepts the filters as a
	dictionary and should return columns and data. It is called by the framework
	every time the report is refreshed or a filter is updated.
	"""
	columns = get_columns()
	data = get_data(filters)

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
			"width": 200,
		},
		{
			"label": _("Required By"),
			"fieldname": "schedule_date",
			"fieldtype": "Date",
			"width": 200,
		},
		{
			"label": _("UOM"),
			"fieldname": "stock_uom",
			"fieldtype": "Link",
			"options": "UOM",
			"width": 200,
		},
		{
			"label": _("Required Qty"),
			"fieldname": "stock_qty",
			"fieldtype": "int",
			"width": 200,
		},
		{
			"label": _("Completed Qty"),
			"fieldname": "ordered_qty",
			"fieldtype": "int",
			"width": 200,
		},
	]


def get_data(filters):
	"""Return data for the report.

	The report data is a list of rows, with each row being a list of cell values.
	"""
	material_request_item = DocType("Material Request Item")
	material_request = DocType("Material Request")
	material_request_status_list = ["Pending", "Partially Ordered", "Partially Received"]
	query = (
		frappe.qb.from_(material_request)
		.left_join(material_request_item)
		.on(material_request_item.parent == material_request.name)
		.select(
			material_request_item.item_code,
			material_request_item.schedule_date,
			material_request_item.stock_uom,
			material_request_item.stock_qty,
			material_request_item.ordered_qty,
		)
		.where(
			(material_request.material_request_type == "Purchase")
			& (material_request.status.isin(material_request_status_list))
		)
	)
	return query.run(as_dict=True)
