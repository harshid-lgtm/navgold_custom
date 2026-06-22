# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Sum
from frappe.utils import flt

from navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku import process_bulk_selling_price_fetch

# (price fieldname, display label) — drives both the price columns and the
# matching variance columns, so the two never drift apart.
STRATEGY_PRICES = [
	("fba_min", "FBA Min"),
	("fba_max", "FBA Max"),
	("sfp_min", "SFP Min"),
	("sfp_max", "SFP Max"),
	("backorder_min", "Backorder Min"),
	("backorder_max", "Backorder Max"),
	("default_min", "Non-Prime Min"),
	("default_max", "Non-Prime Max"),
]


def execute(filters: dict | None = None):
	# Each rebuild refreshes listing prices from Amazon first, hence the Prepared-report flag.
	refresh_amazon_listing_prices()
	return get_columns(), get_data(filters)


def refresh_amazon_listing_prices():
	active_proxy_skus = frappe.get_all("Proxy SKU", filters={"is_active": 1}, pluck="name")
	if not active_proxy_skus:
		return

	process_bulk_selling_price_fetch(active_proxy_skus)
	frappe.db.commit()


def get_columns() -> list[dict]:
	columns = [
		{
			"label": _("Proxy SKU"),
			"fieldname": "proxy_sku",
			"fieldtype": "Link",
			"options": "Proxy SKU",
			"width": 180,
		},
		{
			"label": _("Is FBA"),
			"fieldname": "is_fba",
			"fieldtype": "Check",
			"width": 80,
		},
	]

	for fieldname, label in STRATEGY_PRICES:
		columns.append({"label": _(label), "fieldname": fieldname, "fieldtype": "Currency", "width": 110})

	columns.append(
		{
			"label": _("Current Amazon Price"),
			"fieldname": "current_selling_price",
			"fieldtype": "Currency",
			"width": 150,
		}
	)

	for fieldname, label in STRATEGY_PRICES:
		columns.append(
			{
				"label": _(f"{label} Var %"),
				"fieldname": f"{fieldname}_var",
				"fieldtype": "Percent",
				"width": 100,
			}
		)

	columns.append({"label": _("AFN Stock"), "fieldname": "afn_stock", "fieldtype": "Float", "width": 100})
	columns.append(
		{"label": _("Other Stock"), "fieldname": "other_stock", "fieldtype": "Float", "width": 110}
	)

	return columns


def get_data(filters: dict | None = None) -> list[dict]:
	pricing = DocType("Amazon Product Pricing")
	proxy_sku = DocType("Proxy SKU")
	fba = DocType("Amazon Product Strategy").as_("fba")
	sfp = DocType("Amazon Product Strategy").as_("sfp")
	backorder = DocType("Amazon Product Strategy").as_("backorder")
	default = DocType("Amazon Product Strategy").as_("non_prime")

	query = (
		frappe.qb.from_(pricing)
		.left_join(proxy_sku)
		.on(pricing.proxy_sku == proxy_sku.name)
		.left_join(fba)
		.on(pricing.fba_strategy == fba.name)
		.left_join(sfp)
		.on(pricing.sfp_strategy == sfp.name)
		.left_join(backorder)
		.on(pricing.backorder_strategy == backorder.name)
		.left_join(default)
		.on(pricing.default_strategy == default.name)
		.select(
			pricing.proxy_sku,
			proxy_sku.item,
			proxy_sku.is_fba,
			fba.calculated_min_price.as_("fba_min"),
			fba.calculated_max_price.as_("fba_max"),
			sfp.calculated_min_price.as_("sfp_min"),
			sfp.calculated_max_price.as_("sfp_max"),
			backorder.calculated_min_price.as_("backorder_min"),
			backorder.calculated_max_price.as_("backorder_max"),
			default.calculated_min_price.as_("default_min"),
			default.calculated_max_price.as_("default_max"),
			proxy_sku.current_selling_price,
		)
		.orderby(pricing.proxy_sku)
	)

	if filters and filters.get("proxy_sku"):
		query = query.where(pricing.proxy_sku == filters.get("proxy_sku"))

	rows = query.run(as_dict=True)

	item_codes = list({row.item for row in rows if row.item})
	afn_stock_map, other_stock_map = get_stock_maps(item_codes)

	for row in rows:
		amazon_price = flt(row.current_selling_price)
		for fieldname, _label in STRATEGY_PRICES:
			price = flt(row.get(fieldname))
			# Blank variance when either side is missing — a 0 price isn't a -100% deviation.
			row[f"{fieldname}_var"] = (
				flt((price - amazon_price) / amazon_price * 100, 2) if amazon_price and price else None
			)

		row["afn_stock"] = flt(afn_stock_map.get(row.item))
		row["other_stock"] = flt(other_stock_map.get(row.item))

	return rows


def get_stock_maps(item_codes: list[str]) -> tuple[dict, dict]:
	if not item_codes:
		return {}, {}

	afn_warehouse = frappe.db.get_single_value("Amazon SP API Settings", "afn_warehouse")
	bin = DocType("Bin")
	warehouse = DocType("Warehouse")

	afn_stock_map = {}
	if afn_warehouse:
		afn_rows = (
			frappe.qb.from_(bin)
			.select(bin.item_code, Sum(bin.actual_qty).as_("qty"))
			.where((bin.item_code.isin(item_codes)) & (bin.warehouse == afn_warehouse))
			.groupby(bin.item_code)
			.run(as_dict=True)
		)
		afn_stock_map = {row.item_code: row.qty for row in afn_rows}

	other_query = (
		frappe.qb.from_(bin)
		.left_join(warehouse)
		.on(bin.warehouse == warehouse.name)
		.select(bin.item_code, Sum(bin.actual_qty).as_("qty"))
		.where((bin.item_code.isin(item_codes)) & (warehouse.is_rejected_warehouse == 0))
		.groupby(bin.item_code)
	)
	if afn_warehouse:
		other_query = other_query.where(bin.warehouse != afn_warehouse)

	other_stock_map = {row.item_code: row.qty for row in other_query.run(as_dict=True)}

	return afn_stock_map, other_stock_map
