# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder import DocType, Order
from frappe.query_builder.functions import Count, Sum
from frappe.utils.data import add_to_date, cint, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("UOM"), "fieldname": "uom", "fieldtype": "Data", "width": 90},
		{"label": _("Amazon SKU(s)"), "fieldname": "sku_display", "fieldtype": "Data", "width": 220},
		{"label": _("ASIN(s)"), "fieldname": "asin_display", "fieldtype": "Data", "width": 160},
		{"label": _("Qty Sold"), "fieldname": "total_qty", "fieldtype": "Float", "width": 100},
		{"label": _("Revenue"), "fieldname": "total_amount", "fieldtype": "Currency", "width": 120},
		{"label": _("Orders"), "fieldname": "order_count", "fieldtype": "Int", "width": 90},
	]


def get_data(filters):
	rows = build_top_selling_rows(
		from_date=filters.get("from_date"),
		to_date=filters.get("to_date"),
		top_n=filters.get("top_n"),
		sort_by=filters.get("sort_by"),
		item_group=filters.get("item_group"),
	)

	for row in rows:
		row["sku_display"] = ", ".join(row["skus"]) or _("(unmapped)")
		row["asin_display"] = ", ".join(row["asins"])
	return rows


def build_top_selling_rows(from_date=None, to_date=None, top_n=None, sort_by=None, item_group=None):
	# Default window is the trailing 30 days; callers pass an explicit range to widen/narrow it.
	from_date = getdate(from_date) if from_date else add_to_date(getdate(), days=-30)
	to_date = getdate(to_date) if to_date else getdate()
	top_n = cint(top_n) or 50
	sort_by = sort_by or "Revenue"

	sales_order = DocType("Sales Order")
	sales_order_item = DocType("Sales Order Item")

	# Sales aren't linked to a specific Proxy SKU, and one Item+UOM can map to several
	# active Proxy SKUs, so we aggregate at the real sales grain (item+uom) to avoid the
	# fan-out double-counting a join would cause, then attach the mapped SKUs afterwards.
	query = (
		frappe.qb.from_(sales_order_item)
		.inner_join(sales_order)
		.on(sales_order_item.parent == sales_order.name)
		.select(
			sales_order_item.item_code.as_("item_code"),
			sales_order_item.uom.as_("uom"),
			Sum(sales_order_item.qty).as_("total_qty"),
			Sum(sales_order_item.amount).as_("total_amount"),
			Count(sales_order.name).distinct().as_("order_count"),
		)
		.where(
			(sales_order.docstatus == 1)
			& (sales_order.transaction_date >= from_date)
			& (sales_order.transaction_date <= to_date)
		)
		.groupby(sales_order_item.item_code, sales_order_item.uom)
	)

	if item_group:
		item = DocType("Item")
		query = (
			query.inner_join(item)
			.on(sales_order_item.item_code == item.name)
			.where(item.item_group == item_group)
		)

	order_field = Sum(sales_order_item.qty) if sort_by == "Qty" else Sum(sales_order_item.amount)
	rows = query.orderby(order_field, order=Order.desc).limit(top_n).run(as_dict=True)

	attach_proxy_skus(rows)
	return rows


def attach_proxy_skus(rows):
	if not rows:
		return

	item_codes = list({row["item_code"] for row in rows})
	proxy_rows = frappe.get_all(
		"Proxy SKU",
		filters={"is_active": 1, "item": ["in", item_codes]},
		fields=["name", "asin", "item", "uom"],
	)
	sku_map = {}
	for proxy_row in proxy_rows:
		sku_map.setdefault((proxy_row["item"], proxy_row["uom"]), []).append(proxy_row)

	for row in rows:
		matches = sku_map.get((row["item_code"], row["uom"]), [])
		row["skus"] = [match["name"] for match in matches]
		row["asins"] = [match["asin"] for match in matches if match.get("asin")]
