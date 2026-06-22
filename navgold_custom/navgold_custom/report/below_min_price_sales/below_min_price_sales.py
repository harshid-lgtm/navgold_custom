# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.utils.data import add_days, cint, flt, now_datetime


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{
			"label": _("Sales Order"),
			"fieldname": "sales_order",
			"fieldtype": "Link",
			"options": "Sales Order",
			"width": 160,
		},
		{"label": _("Created"), "fieldname": "creation", "fieldtype": "Datetime", "width": 150},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("UOM"), "fieldname": "uom", "fieldtype": "Data", "width": 80},
		{
			"label": _("Proxy SKU"),
			"fieldname": "proxy_sku",
			"fieldtype": "Link",
			"options": "Proxy SKU",
			"width": 150,
		},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 70},
		{"label": _("Sold Rate"), "fieldname": "rate", "fieldtype": "Currency", "width": 100},
		{"label": _("Min Price"), "fieldname": "min_price", "fieldtype": "Currency", "width": 100},
		{"label": _("Deficit"), "fieldname": "deficit", "fieldtype": "Currency", "width": 100},
		{"label": _("Est. Loss"), "fieldname": "estimated_loss", "fieldtype": "Currency", "width": 110},
	]


def get_data(filters):
	days_back = cint(filters.get("days_back")) or 3
	sales_order = DocType("Sales Order")
	sales_order_item = DocType("Sales Order Item")

	so_rows = (
		frappe.qb.from_(sales_order_item)
		.join(sales_order)
		.on(sales_order_item.parent == sales_order.name)
		.select(
			sales_order.name.as_("sales_order"),
			sales_order.creation,
			sales_order_item.item_code,
			sales_order_item.uom,
			sales_order_item.qty,
			sales_order_item.rate,
		)
		.where(
			(sales_order.docstatus == 1)
			& (sales_order.amazon_order_id.isnotnull())
			& (sales_order.creation > add_days(now_datetime(), -days_back))
		)
	).run(as_dict=True)

	min_price_map = get_min_price_map({(row.item_code, row.uom) for row in so_rows})
	rows = build_below_min_rows(so_rows, min_price_map)

	if cint(filters.get("only_below_min", 1)):
		rows = [row for row in rows if row["deficit"] > 0]

	minimum_deficit = flt(filters.get("minimum_deficit"))
	if minimum_deficit:
		rows = [row for row in rows if row["deficit"] >= minimum_deficit]

	rows.sort(key=lambda row: row["estimated_loss"], reverse=True)
	return rows


def get_min_price_map(item_uom_pairs):
	if not item_uom_pairs:
		return {}

	proxy_sku = DocType("Proxy SKU")
	pricing = DocType("Amazon Product Pricing")
	item_codes = {pair[0] for pair in item_uom_pairs}

	rows = (
		frappe.qb.from_(pricing)
		.join(proxy_sku)
		.on(pricing.proxy_sku == proxy_sku.name)
		.select(proxy_sku.item, proxy_sku.uom, proxy_sku.name.as_("proxy_sku"), pricing.current_min_price)
		.where(
			(proxy_sku.item.isin(item_codes))
			& (proxy_sku.is_active == 1)
			& (pricing.is_active == 1)
			& (pricing.current_min_price > 0)
		)
	).run(as_dict=True)

	# Multiple active proxies can share an item+uom; keep the lowest min price (most
	# conservative comparison) so a sale is only flagged when below every proxy's floor.
	min_price_map = {}
	for row in rows:
		key = (row.item, row.uom)
		existing = min_price_map.get(key)
		if existing is None or flt(row.current_min_price) < existing["min_price"]:
			min_price_map[key] = {"min_price": flt(row.current_min_price), "proxy_sku": row.proxy_sku}
	return min_price_map


def build_below_min_rows(so_rows, min_price_map):
	rows = []
	for so_row in so_rows:
		mapped = min_price_map.get((so_row.item_code, so_row.uom))
		if not mapped:
			continue

		deficit = flt(mapped["min_price"] - flt(so_row.rate), 2)
		rows.append(
			{
				"sales_order": so_row.sales_order,
				"creation": so_row.creation,
				"item_code": so_row.item_code,
				"uom": so_row.uom,
				"proxy_sku": mapped["proxy_sku"],
				"qty": flt(so_row.qty),
				"rate": flt(so_row.rate),
				"min_price": mapped["min_price"],
				"deficit": deficit,
				"estimated_loss": flt(max(deficit, 0) * flt(so_row.qty), 2),
			}
		)
	return rows
