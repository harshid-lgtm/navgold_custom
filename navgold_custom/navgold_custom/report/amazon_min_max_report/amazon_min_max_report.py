# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import math

import frappe
from frappe import _
from frappe.query_builder import Case, DocType
from frappe.query_builder.functions import Max, Sum
from frappe.utils import add_days, cint, flt, today


def execute(filters: dict | None = None):
	filters = filters or {}
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns() -> list[dict]:
	return [
		{
			"label": _("Item Code"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 180,
		},
		{
			"label": _("Item Name"),
			"fieldname": "item_name",
			"fieldtype": "Data",
			"width": 200,
		},
		{
			"label": _("Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 160,
		},
		{
			"label": _("Pack Size"),
			"fieldname": "pack_size",
			"fieldtype": "Float",
			"width": 100,
		},
		{
			"label": _("Sales (7D)"),
			"fieldname": "sales_7d",
			"fieldtype": "Float",
			"width": 100,
		},
		{
			"label": _("Sales (30D)"),
			"fieldname": "sales_30d",
			"fieldtype": "Float",
			"width": 110,
		},
		{
			"label": _("Packs Sold (30D)"),
			"fieldname": "packs_sold_30",
			"fieldtype": "Float",
			"width": 140,
		},
		{
			"label": _("Min Stock Level"),
			"fieldname": "min_stock_level",
			"fieldtype": "Float",
			"width": 140,
		},
		{
			"label": _("Max Stock Level"),
			"fieldname": "max_stock_level",
			"fieldtype": "Float",
			"width": 140,
		},
	]


def get_data(filters: dict) -> list[dict]:
	min_cover_days = cint(filters.get("min_cover_days") or 4)
	max_multiplier = cint(filters.get("max_multiplier") or 3)
	warehouse = filters.get("warehouse") or ""

	items = get_items_with_pack_size()
	if not items:
		return []

	today_date = today()
	combined_sales_map = get_combined_sales_map(today_date)
	max_pack_by_base = get_max_pack_by_base_sku()

	results = []
	for item in items:
		item_code = item["item_code"]
		pack_size = flt(item["pack_size"]) or 1.0
		base_sku = item["variant_of"]

		sales_7d, sales_30d = combined_sales_map.get(item_code, (0.0, 0.0))

		# MAX_PACK_QTY: largest pack size within the same base SKU
		max_pack_qty = flt(max_pack_by_base.get(base_sku, pack_size)) if base_sku else pack_size

		min_val, max_val = calculate_min_max(
			sales_7d, sales_30d, pack_size, max_pack_qty, min_cover_days, max_multiplier
		)

		packs_sold_30 = sales_30d / pack_size if pack_size else 0

		results.append(
			{
				"item_code": item_code,
				"item_name": item["item_name"],
				"warehouse": warehouse,
				"pack_size": pack_size,
				"sales_7d": sales_7d,
				"sales_30d": sales_30d,
				"packs_sold_30": packs_sold_30,
				"min_stock_level": min_val,
				"max_stock_level": max_val,
			}
		)

	return results


def calculate_min_max(
	sales_7d: float,
	sales_30d: float,
	pack_size: float,
	max_pack_qty: float,
	min_cover_days: int,
	max_multiplier: int,
) -> tuple[float, float]:
	pack_size = pack_size or 1

	packs_sold_30 = sales_30d / pack_size
	prelim_min = round((packs_sold_30 / 31) * min_cover_days) * pack_size

	# Low-volume suppression
	if 0 < packs_sold_30 < 5:
		prelim_min = 0

	#  No recent demand suppression
	if sales_7d == 0:
		prelim_min = 0

	# Step 5: Calculate Max
	max_val = prelim_min * max_multiplier

	# slow movers that must still be stocked
	if sales_7d == 0 and packs_sold_30 >= 5:
		override_min = round((packs_sold_30 / 31) * 6) * pack_size
		override_max = override_min * 2
		if override_max > max_val:
			max_val = override_max

	if max_val > 0 and max_val < max_pack_qty:
		max_val = max_pack_qty
	min_val = math.ceil(max_val / 2) if max_val > 0 else 0

	return min_val, max_val


def get_items_with_pack_size() -> list[dict]:
	item = DocType("Item")
	uom_detail = DocType("UOM Conversion Detail")

	return (
		frappe.qb.from_(item)
		.left_join(uom_detail)
		.on((uom_detail.parent == item.name) & (uom_detail.uom == item.purchase_uom))
		.select(
			item.name.as_("item_code"),
			item.item_name,
			item.variant_of,
			uom_detail.conversion_factor.as_("pack_size"),
		)
		.where((item.disabled == 0) & (item.is_stock_item == 1))
	).run(as_dict=True)


def get_combined_sales_map(today_date: str) -> dict[str, tuple[float, float]]:
	sales_order = DocType("Sales Order")
	sales_order_item = DocType("Sales Order Item")
	item = DocType("Item")

	date_7d = add_days(today_date, -7)
	date_30d = add_days(today_date, -30)

	rows = (
		frappe.qb.from_(sales_order)
		.join(sales_order_item)
		.on(sales_order_item.parent == sales_order.name)
		.join(item)
		.on((item.name == sales_order_item.item_code) & (item.disabled == 0) & (item.is_stock_item == 1))
		.select(
			sales_order_item.item_code,
			Sum(Case().when(sales_order.transaction_date >= date_7d, sales_order_item.qty).else_(0)).as_(
				"sales_7d"
			),
			Sum(sales_order_item.qty).as_("sales_30d"),
		)
		.where(
			(sales_order.docstatus == 1)
			& (sales_order.transaction_date >= date_30d)
			& (sales_order.transaction_date <= today_date)
		)
		.groupby(sales_order_item.item_code)
	).run(as_dict=True)

	return {r["item_code"]: (flt(r["sales_7d"]), flt(r["sales_30d"])) for r in rows}


def get_max_pack_by_base_sku() -> dict[str, float]:
	item = DocType("Item")
	uom_detail = DocType("UOM Conversion Detail")

	rows = (
		frappe.qb.from_(item)
		.join(uom_detail)
		.on((uom_detail.parent == item.name) & (uom_detail.uom == item.purchase_uom))
		.select(
			item.variant_of,
			Max(uom_detail.conversion_factor).as_("max_pack_qty"),
		)
		.where(
			(item.disabled == 0)
			& (item.is_stock_item == 1)
			& (item.variant_of.isnotnull())
			& (item.variant_of != "")
		)
		.groupby(item.variant_of)
	).run(as_dict=True)

	return {r["variant_of"]: flt(r["max_pack_qty"]) for r in rows}
