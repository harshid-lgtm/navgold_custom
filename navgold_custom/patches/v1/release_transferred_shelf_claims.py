# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.query_builder import DocType
from frappe.utils.data import flt


def execute():
	stranded_rows = get_stranded_rows()
	if not stranded_rows:
		return

	report_released_rows(stranded_rows)

	for row in stranded_rows:
		frappe.db.set_value(
			"Pick List Item",
			row.name,
			"delivered_qty",
			flt(row.transferred_qty),
			update_modified=False,
		)


def get_stranded_rows():
	pick_list = DocType("Pick List")
	pick_list_item = DocType("Pick List Item")

	return (
		frappe.qb.from_(pick_list_item)
		.inner_join(pick_list)
		.on(pick_list.name == pick_list_item.parent)
		.select(
			pick_list_item.name,
			pick_list_item.parent,
			pick_list_item.item_code,
			pick_list_item.warehouse,
			pick_list_item.picked_qty,
			pick_list_item.delivered_qty,
			pick_list_item.transferred_qty,
		)
		.where(
			(pick_list.purpose != "Delivery")
			& (pick_list_item.docstatus == 1)
			& (pick_list_item.delivered_qty != pick_list_item.transferred_qty)
		)
	).run(as_dict=True)


def report_released_rows(stranded_rows):
	released_qty_by_pick_list = {}
	for row in stranded_rows:
		released_qty = flt(row.transferred_qty) - flt(row.delivered_qty)
		released_qty_by_pick_list[row.parent] = released_qty_by_pick_list.get(row.parent, 0) + released_qty

	print(f"Releasing {len(stranded_rows)} transferred pick list rows back onto the shelf:")
	for pick_list_name, released_qty in sorted(released_qty_by_pick_list.items()):
		print(f"  {pick_list_name}: {released_qty} units")
