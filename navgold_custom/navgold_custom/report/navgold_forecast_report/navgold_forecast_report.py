# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import math
import re

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count, Sum
from frappe.utils import add_days, ceil, cint, cstr, flt, today
from frappe.utils.csvutils import read_csv_content
from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

from navgold_custom.navgold_custom.custom_script.pick_list.pick_list import (
	get_pickable_stock_qty,
	get_whole_number_uoms,
)
from navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku import get_proxy_sku_map
from navgold_custom.navgold_custom.erpnext_compat import get_product_bundle_items
from navgold_custom.navgold_custom.utils import get_conversion_factor, get_free_qty_map


def execute(filters=None):
	filters = filters or {}
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{"label": _("SKU"), "fieldname": "sku", "fieldtype": "Link", "options": "Proxy SKU", "width": 160},
		{"label": _("FNSKU"), "fieldname": "fnsku", "fieldtype": "Data", "width": 130},
		{"label": _("Brand"), "fieldname": "brand", "fieldtype": "Link", "options": "Brand", "width": 120},
		{"label": _("Title"), "fieldname": "title", "fieldtype": "Data", "width": 220},
		{
			"label": _("Variant Multiplier"),
			"fieldname": "variant_multiplier",
			"fieldtype": "Float",
			"width": 130,
		},
		{"label": _("ASIN"), "fieldname": "asin", "fieldtype": "Data", "width": 130},
		{
			"label": _("Sales Rank Current"),
			"fieldname": "sales_rank_current",
			"fieldtype": "Int",
			"width": 130,
		},
		{
			"label": _("FBA Fulfillable Stock"),
			"fieldname": "fba_fulfillable_stock",
			"fieldtype": "Float",
			"width": 150,
		},
		{"label": _("eBay Sales"), "fieldname": "ebay_sales", "fieldtype": "Float", "width": 100},
		{"label": _("FBA Sales"), "fieldname": "fba_sales", "fieldtype": "Float", "width": 100},
		{"label": _("SFP Sales"), "fieldname": "sfp_sales", "fieldtype": "Float", "width": 100},
		{"label": _("Total Sales"), "fieldname": "total_sales", "fieldtype": "Float", "width": 100},
		{"label": _("Sales 3D"), "fieldname": "sales_3d", "fieldtype": "Float", "width": 90},
		{"label": _("Sales 7D"), "fieldname": "sales_7d", "fieldtype": "Float", "width": 90},
		{"label": _("Sales 14D"), "fieldname": "sales_14d", "fieldtype": "Float", "width": 90},
		{"label": _("Sales 30D"), "fieldname": "sales_30d", "fieldtype": "Float", "width": 90},
		{"label": _("Free Stock"), "fieldname": "free_stock", "fieldtype": "Float", "width": 100},
		{"label": _("Instocks"), "fieldname": "instocks", "fieldtype": "Float", "width": 100},
		{"label": _("Receiving"), "fieldname": "receiving", "fieldtype": "Float", "width": 100},
		{"label": _("Current"), "fieldname": "current", "fieldtype": "Float", "width": 100},
		{
			"label": _("Highest Weekly Rate"),
			"fieldname": "highest_weekly_rate",
			"fieldtype": "Float",
			"width": 150,
		},
		{
			"label": _("Previous Highest Weekly Rate"),
			"fieldname": "previous_highest_weekly_rate",
			"fieldtype": "Float",
			"width": 180,
		},
		{
			"label": _("3 Week Lead Time Stock"),
			"fieldname": "three_week_lead_time_stock",
			"fieldtype": "Float",
			"width": 170,
		},
		{"label": _("30D Revenue"), "fieldname": "revenue_30d", "fieldtype": "Currency", "width": 120},
		{
			"label": _("Buy Box Current"),
			"fieldname": "buy_box_current",
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"label": _("Fulfilment Cost Type"),
			"fieldname": "fulf_cost_type",
			"fieldtype": "Data",
			"width": 140,
		},
		{"label": _("Fulfilment Cost"), "fieldname": "fulf_cost", "fieldtype": "Currency", "width": 120},
		{"label": _("Your Price"), "fieldname": "your_price", "fieldtype": "Currency", "width": 100},
		{"label": _("Min Price"), "fieldname": "min_price", "fieldtype": "Currency", "width": 100},
		{"label": _("Min Margin"), "fieldname": "min_margin", "fieldtype": "Percent", "width": 100},
		{"label": _("Strategy"), "fieldname": "strategy", "fieldtype": "Data", "width": 90},
		{
			"label": _("Top Supplier"),
			"fieldname": "top_supplier",
			"fieldtype": "Link",
			"options": "Supplier",
			"width": 140,
		},
		{
			"label": _("Top Supplier Cost"),
			"fieldname": "top_supplier_cost",
			"fieldtype": "Currency",
			"width": 140,
		},
		{
			"label": _("Current Month Offer"),
			"fieldname": "current_month_offer",
			"fieldtype": "Data",
			"width": 150,
		},
		{"label": _("Next Month Offer"), "fieldname": "next_month_offer", "fieldtype": "Data", "width": 150},
		{
			"label": _("4 Week Forecast"),
			"fieldname": "four_week_forecast",
			"fieldtype": "Float",
			"width": 130,
		},
		{"label": _("Forecast"), "fieldname": "forecast", "fieldtype": "Float", "width": 100},
		{"label": _("Ranking"), "fieldname": "ranking", "fieldtype": "Data", "width": 90},
		{"label": _("Sales Dropped"), "fieldname": "sales_dropped", "fieldtype": "Data", "width": 110},
		{"label": _("Program Forecast"), "fieldname": "program_forecast", "fieldtype": "Data", "width": 140},
		{"label": _("Program Reason"), "fieldname": "program_reason", "fieldtype": "Data", "width": 200},
		{"label": _("Total Forecast"), "fieldname": "total_forecast", "fieldtype": "Float", "width": 120},
		{"label": _("PO Count"), "fieldname": "po_count", "fieldtype": "Int", "width": 90},
		{
			"label": _("Total Qty on Order"),
			"fieldname": "total_qty_on_order",
			"fieldtype": "Float",
			"width": 150,
		},
		{"label": _("Comment"), "fieldname": "comment", "fieldtype": "Data", "width": 100},
		{
			"label": _("Quantity to Transfer"),
			"fieldname": "quantity_to_transfer",
			"fieldtype": "Float",
			"width": 140,
		},
	]


def get_data(filters):
	asset = frappe.get_cached_doc("Navgold Forecast Asset").as_dict()

	fba_map = parse_fba_inventory(asset.get("fba_inventory_csv"))
	sales_3d_map = parse_sales_csv(asset.get("sales_3d_csv"))
	sales_7d_map = parse_sales_csv(asset.get("sales_7d_csv"))
	sales_14d_map = parse_sales_csv(asset.get("sales_14d_csv"))
	sales_30d_map = parse_sales_csv(asset.get("sales_30d_csv"))
	revenue_map = parse_30d_revenue(asset.get("sales_30d_csv"))
	keepa_map = parse_keepa(asset.get("keepa_xlsx"))
	your_price_map = parse_your_price(asset.get("your_price_xlsx"))
	prev_rate_map = parse_previous_report(asset.get("previous_report_xlsx"))

	proxy_sku_map = get_proxy_sku_map()
	if not proxy_sku_map:
		return []

	item_codes = list({v["item"] for v in proxy_sku_map.values() if v.get("item")})
	proxy_sku_names = list(proxy_sku_map.keys())

	item_info_map = get_item_info_map(item_codes)
	variant_mult_map = get_variant_multiplier_map(proxy_sku_map)
	free_qty_map = get_free_qty_map(item_codes) if item_codes else {}
	supplier_map = get_top_supplier_map(item_codes)
	pricing_map = get_pricing_map(proxy_sku_names)
	purchase_order_map = get_po_data_map(item_codes)
	channel_sales_map = get_sales_by_channel(item_codes)
	instocks_map = get_instocks_map(item_codes)
	current_map = get_current_picking_map(item_codes)

	data = []
	for sku, sku_info in proxy_sku_map.items():
		item_code = sku_info.get("item") or ""
		asin = sku_info.get("asin") or ""
		item_info = item_info_map.get(item_code, {})

		fba = fba_map.get(sku, {})
		s3 = flt(sales_3d_map.get(sku, {}).get("units", 0))
		s7 = flt(sales_7d_map.get(sku, {}).get("units", 0))
		s14 = flt(sales_14d_map.get(sku, {}).get("units", 0))
		s30 = flt(sales_30d_map.get(sku, {}).get("units", 0))
		rev_30d = flt(revenue_map.get(sku, {}).get("revenue", 0))
		keepa = keepa_map.get(asin, {})
		your_price = flt(your_price_map.get(sku, {}).get("price", 0))
		prev_rate = flt(prev_rate_map.get(sku, {}).get("highest_weekly_rate", 0))

		pricing = pricing_map.get(sku, {})
		supplier = supplier_map.get(item_code, {})
		purchase_order = purchase_order_map.get(item_code, {})
		free_stock = flt(free_qty_map.get(item_code, {}).get("free_qty", 0))

		fba_stock = flt(fba.get("available", 0))
		fnsku = fba.get("fnsku") or sku_info.get("fnsku") or ""
		receiving = flt(fba.get("receiving", 0))

		channel_sales = channel_sales_map.get(item_code, {})
		fba_sales = flt(channel_sales.get("fba_sales", 0))
		sfp_sales = flt(channel_sales.get("sfp_sales", 0))
		total_sales = fba_sales + sfp_sales

		instocks = flt(instocks_map.get(item_code, 0))
		current = flt(current_map.get(item_code, 0))

		current_month_offer = supplier.get("current_month_offer", "")
		next_month_offer = supplier.get("next_month_offer", "")

		highest_weekly_rate = compute_highest_weekly_rate(s7, s14, s30)
		three_week_lt = compute_3week_lead_time_stock(fba_stock, highest_weekly_rate)
		strategy = "FBA" if fba_stock > 0 else "SFP"
		comment = compute_comment(asin, s3, s7, s14, s30)
		four_week_forecast = highest_weekly_rate * 4

		forecast = compute_forecast(highest_weekly_rate, instocks, current, receiving, three_week_lt)
		ranking = compute_ranking(keepa.get("sales_rank", 0))
		sales_dropped = compute_sales_dropped(fba_stock, s7, highest_weekly_rate, prev_rate)
		program_forecast, program_reason = compute_program_forecast(
			keepa.get("buy_box", 0),
			fba_stock,
			s3,
			s7,
			highest_weekly_rate,
			instocks,
			current,
			receiving,
			forecast,
			three_week_lt,
			pricing.get("min_margin", 0),
		)
		total_forecast = compute_total_forecast(highest_weekly_rate, forecast)

		row = {
			"sku": sku,
			"fnsku": fnsku,
			"brand": item_info.get("brand", ""),
			"title": item_info.get("item_name", ""),
			"variant_multiplier": flt(variant_mult_map.get(sku, 1)),
			"asin": asin,
			"sales_rank_current": keepa.get("sales_rank", 0),
			"fba_fulfillable_stock": fba_stock,
			"ebay_sales": 0,
			"fba_sales": fba_sales,
			"sfp_sales": sfp_sales,
			"total_sales": total_sales,
			"sales_3d": s3,
			"sales_7d": s7,
			"sales_14d": s14,
			"sales_30d": s30,
			"free_stock": free_stock,
			"instocks": instocks,
			"receiving": receiving,
			"current": current,
			"highest_weekly_rate": highest_weekly_rate,
			"previous_highest_weekly_rate": prev_rate,
			"three_week_lead_time_stock": three_week_lt,
			"revenue_30d": rev_30d,
			"buy_box_current": flt(keepa.get("buy_box", 0)),
			"fulf_cost_type": "",
			"fulf_cost": 0,
			"your_price": your_price,
			"min_price": flt(pricing.get("min_price", 0)),
			"min_margin": flt(pricing.get("min_margin", 0)),
			"strategy": strategy,
			"top_supplier": supplier.get("supplier", ""),
			"top_supplier_cost": flt(supplier.get("price", 0)),
			"current_month_offer": current_month_offer,
			"next_month_offer": next_month_offer,
			"four_week_forecast": four_week_forecast,
			"forecast": forecast,
			"ranking": ranking,
			"sales_dropped": sales_dropped,
			"program_forecast": program_forecast,
			"program_reason": program_reason,
			"total_forecast": total_forecast,
			"po_count": purchase_order.get("po_count", 0),
			"total_qty_on_order": flt(purchase_order.get("total_qty", 0)),
			"comment": comment,
			"quantity_to_transfer": 0,
		}
		data.append(row)

	if filters.get("brand"):
		data = [row for row in data if row["brand"] == filters["brand"]]
	if filters.get("strategy"):
		data = [row for row in data if row["strategy"] == filters["strategy"]]
	if filters.get("comment"):
		data = [row for row in data if row["comment"] == filters["comment"]]

	return data


def read_file_content(file_url):
	if not file_url:
		return None
	try:
		file_doc = frappe.get_doc("File", {"file_url": file_url})
		return file_doc.get_content()
	except Exception:
		frappe.log_error(f"Failed to read file: {file_url}", "Navgold Forecast Report")
		return None


def read_csv_as_dicts(file_url):
	content = read_file_content(file_url)
	if not content:
		return []

	rows = read_csv_content(content)
	if not rows or len(rows) < 2:
		return []

	headers = [cstr(h or "").strip() for h in rows[0]]
	return [dict(zip(headers, row, strict=False)) for row in rows[1:]]


def read_xlsx_as_dicts(file_url):
	content = read_file_content(file_url)
	if not content:
		return []

	try:
		rows = read_xlsx_file_from_attached_file(fcontent=content)
		if not rows or len(rows) < 2:
			return []

		headers = [cstr(h or "").strip() for h in rows[0]]
		return [dict(zip(headers, row, strict=False)) for row in rows[1:]]
	except Exception:
		frappe.log_error(f"Failed to parse XLSX: {file_url}", "Navgold Forecast Report")
		return []


def get_col_value(row, *possible_keys, default=None):
	lower_row = {cstr(k).strip().lower(): v for k, v in row.items() if k}
	for key in possible_keys:
		val = lower_row.get(key.lower())
		if val is not None:
			return val
	return default


def parse_fba_inventory(file_url):
	result = {}
	for row in read_csv_as_dicts(file_url):
		sku = cstr(get_col_value(row, "sku", "SKU", default="")).strip()
		if not sku:
			continue
		result[sku] = {
			"available": flt(get_col_value(row, "available", default=0)),
			"fnsku": cstr(get_col_value(row, "fnsku", "FNSKU", default="")).strip(),
			"receiving": (
				flt(get_col_value(row, "inbound-working", default=0))
				+ flt(get_col_value(row, "inbound-shipped", default=0))
				+ flt(get_col_value(row, "inbound-received", default=0))
			),
		}
	return result


def parse_sales_csv(file_url):
	result = {}
	for row in read_csv_as_dicts(file_url):
		sku = cstr(get_col_value(row, "SKU", "sku", default="")).strip()
		if not sku or sku.lower() == "sku":
			continue
		units = flt(strip_currency(get_col_value(row, "Units ordered", default=0)))
		result[sku] = {"units": units}
	return result


def parse_30d_revenue(file_url):
	result = {}
	for row in read_csv_as_dicts(file_url):
		sku = cstr(get_col_value(row, "SKU", "sku", default="")).strip()
		if not sku or sku.lower() == "sku":
			continue
		revenue = flt(strip_currency(get_col_value(row, "Ordered Product Sales", default=0)))
		result[sku] = {"revenue": revenue}
	return result


def parse_keepa(file_url):
	rows = read_xlsx_as_dicts(file_url)
	if not rows:
		return {}

	result = {}
	for row in rows:
		asin = cstr(get_col_value(row, "ASIN", "asin", default="")).strip()
		if not asin:
			continue

		buy_box = 0
		sales_rank = 0
		lower_row = {cstr(k).strip().lower(): v for k, v in row.items() if k}
		for key, val in lower_row.items():
			if "buy box" in key and "current" in key:
				buy_box = flt(val)
			elif "sales rank" in key and "current" in key:
				sales_rank = int(flt(val)) if val else 0

		result[asin] = {"buy_box": buy_box, "sales_rank": sales_rank}
	return result


def parse_your_price(file_url):
	rows = read_xlsx_as_dicts(file_url)
	if not rows:
		return {}

	result = {}
	for row in rows:
		sku = cstr(
			get_col_value(row, "seller-sku", "sku", "seller sku", "Seller SKU", "SKU", default="")
		).strip()
		if not sku:
			continue
		price = flt(get_col_value(row, "price", "your-price", "your price", "Your Price", "Price", default=0))
		result[sku] = {"price": price}
	return result


def parse_previous_report(file_url):
	rows = read_xlsx_as_dicts(file_url)
	if not rows:
		return {}

	result = {}
	for row in rows:
		sku = cstr(get_col_value(row, "sku", "SKU", "seller_sku", default="")).strip()
		if not sku:
			continue

		rate = 0
		lower_row = {cstr(k).strip().lower(): v for k, v in row.items() if k}
		for key, val in lower_row.items():
			if "highest" in key and "weekly" in key and "rate" in key:
				rate = flt(val)
				break

		result[sku] = {"highest_weekly_rate": rate}
	return result


def strip_currency(val):
	if not val:
		return 0
	if isinstance(val, int | float):
		return val
	return re.sub(r"[£$,]", "", cstr(val).strip())


def get_item_info_map(item_codes):
	if not item_codes:
		return {}

	item = DocType("Item")
	rows = (
		frappe.qb.from_(item).select(item.name, item.brand, item.item_name).where(item.name.isin(item_codes))
	).run(as_dict=True)

	return {row["name"]: {"brand": row["brand"] or "", "item_name": row["item_name"] or ""} for row in rows}


def get_sales_by_channel(item_codes):
	if not item_codes:
		return {}

	sales_order = DocType("Sales Order")
	sales_order_item = DocType("Sales Order Item")
	thirty_days_ago = add_days(today(), -30)

	rows = (
		frappe.qb.from_(sales_order)
		.join(sales_order_item)
		.on(sales_order_item.parent == sales_order.name)
		.select(
			sales_order_item.item_code,
			sales_order.fulfillment_channel,
			Sum(sales_order_item.qty).as_("total_qty"),
		)
		.where(
			(sales_order.docstatus == 1)
			& (sales_order.transaction_date >= thirty_days_ago)
			& (sales_order.fulfillment_channel.isin(["AFN", "MFN"]))
			& (sales_order_item.item_code.isin(item_codes))
		)
		.groupby(sales_order_item.item_code, sales_order.fulfillment_channel)
	).run(as_dict=True)

	result = {}
	for row in rows:
		item_code = row["item_code"]
		if item_code not in result:
			result[item_code] = {"fba_sales": 0, "sfp_sales": 0}
		if row["fulfillment_channel"] == "AFN":
			result[item_code]["fba_sales"] = flt(row["total_qty"])
		elif row["fulfillment_channel"] == "MFN":
			result[item_code]["sfp_sales"] = flt(row["total_qty"])
	return result


def get_variant_multiplier_map(proxy_sku_map):
	if not proxy_sku_map:
		return {}

	uom_conversion_detail = DocType("UOM Conversion Detail")

	pairs = [(v["item"], v["uom"]) for v in proxy_sku_map.values() if v.get("item") and v.get("uom")]
	if not pairs:
		return {}

	item_codes = list({p[0] for p in pairs})

	rows = (
		frappe.qb.from_(uom_conversion_detail)
		.select(
			uom_conversion_detail.parent, uom_conversion_detail.uom, uom_conversion_detail.conversion_factor
		)
		.where((uom_conversion_detail.parenttype == "Item") & (uom_conversion_detail.parent.isin(item_codes)))
	).run(as_dict=True)

	uom_lookup = {}
	for row in rows:
		uom_lookup[(row["parent"], row["uom"])] = flt(row["conversion_factor"])

	result = {}
	for sku, info in proxy_sku_map.items():
		key = (info.get("item"), info.get("uom"))
		if key in uom_lookup:
			result[sku] = uom_lookup[key]

	return result


def get_top_supplier_map(item_codes):
	if not item_codes:
		return {}

	supplier_item_price = DocType("Supplier Item Price and Stock")
	rows = (
		frappe.qb.from_(supplier_item_price)
		.select(
			supplier_item_price.item,
			supplier_item_price.supplier,
			supplier_item_price.price,
			supplier_item_price.uom,
		)
		.where((supplier_item_price.item.isin(item_codes)) & (supplier_item_price.rank == 1))
	).run(as_dict=True)

	result = {
		row["item"]: {"supplier": row["supplier"], "price": flt(row["price"]), "uom": row["uom"]}
		for row in rows
	}

	supplier_offer = DocType("Supplier Offer")
	offer_rows = (
		frappe.qb.from_(supplier_item_price)
		.join(supplier_offer)
		.on(supplier_offer.parent == supplier_item_price.name)
		.select(supplier_item_price.item, supplier_offer.offer, supplier_offer.idx)
		.where((supplier_item_price.item.isin(item_codes)) & (supplier_item_price.rank == 1))
		.orderby(supplier_offer.idx)
	).run(as_dict=True)

	for row in offer_rows:
		item_code = row["item"]
		if item_code not in result:
			continue
		if row["idx"] == 1:
			result[item_code]["current_month_offer"] = row["offer"] or ""
		elif row["idx"] == 2:
			result[item_code]["next_month_offer"] = row["offer"] or ""

	return result


def get_instocks_map(item_codes):
	if not item_codes:
		return {}

	purchase_order = DocType("Purchase Order")
	purchase_order_item = DocType("Purchase Order Item")

	rows = (
		frappe.qb.from_(purchase_order)
		.join(purchase_order_item)
		.on(purchase_order_item.parent == purchase_order.name)
		.select(
			purchase_order_item.item_code,
			Sum(purchase_order_item.qty - purchase_order_item.received_qty).as_("outstanding_qty"),
		)
		.where(
			(purchase_order.docstatus == 1)
			& (purchase_order.status.notin(["Completed", "Cancelled", "Closed"]))
			& (purchase_order_item.item_code.isin(item_codes))
		)
		.groupby(purchase_order_item.item_code)
	).run(as_dict=True)

	return {r["item_code"]: flt(r["outstanding_qty"]) for r in rows}


def get_current_picking_map(item_codes):
	if not item_codes:
		return {}

	pick_list = DocType("Pick List")
	pick_list_item = DocType("Pick List Item")

	rows = (
		frappe.qb.from_(pick_list)
		.join(pick_list_item)
		.on(pick_list_item.parent == pick_list.name)
		.select(pick_list_item.item_code, Sum(pick_list_item.qty).as_("total_qty"))
		.where(
			(pick_list.docstatus == 1)
			& (pick_list.purpose == "Material Transfer")
			& (pick_list.status.isin(["Open", "Partly Delivered"]))
			& (pick_list_item.item_code.isin(item_codes))
		)
		.groupby(pick_list_item.item_code)
	).run(as_dict=True)

	return {r["item_code"]: flt(r["total_qty"]) for r in rows}


def get_pricing_map(proxy_sku_names):
	if not proxy_sku_names:
		return {}

	amazon_product_pricing = DocType("Amazon Product Pricing")
	amazon_product_strategy = DocType("Amazon Product Strategy")

	rows = (
		frappe.qb.from_(amazon_product_pricing)
		.left_join(amazon_product_strategy)
		.on(amazon_product_pricing.active_strategy == amazon_product_strategy.name)
		.select(
			amazon_product_pricing.proxy_sku,
			amazon_product_pricing.current_min_price,
			amazon_product_strategy.min_margin,
		)
		.where(
			(amazon_product_pricing.proxy_sku.isin(proxy_sku_names)) & (amazon_product_pricing.is_active == 1)
		)
	).run(as_dict=True)

	return {
		r["proxy_sku"]: {
			"min_price": flt(r["current_min_price"]),
			"min_margin": flt(r["min_margin"]),
		}
		for r in rows
	}


def get_po_data_map(item_codes):
	if not item_codes:
		return {}

	purchase_order = DocType("Purchase Order")
	purchase_order_item = DocType("Purchase Order Item")

	rows = (
		frappe.qb.from_(purchase_order)
		.join(purchase_order_item)
		.on(purchase_order_item.parent == purchase_order.name)
		.select(
			purchase_order_item.item_code,
			Count(purchase_order.name).as_("po_count"),
			Sum(purchase_order_item.qty).as_("total_qty"),
		)
		.where(
			(purchase_order.docstatus == 1)
			& (purchase_order.status.notin(["Completed", "Cancelled", "Closed"]))
			& (purchase_order_item.item_code.isin(item_codes))
		)
		.groupby(purchase_order_item.item_code)
	).run(as_dict=True)

	return {
		r["item_code"]: {
			"po_count": r["po_count"] or 0,
			"total_qty": flt(r["total_qty"]),
		}
		for r in rows
	}


def compute_highest_weekly_rate(s7, s14, s30):
	week1 = flt(s7)
	week2 = max(0, flt(s14) - flt(s7))
	weeks3_4 = max(0, flt(s30) - flt(s14)) / 2
	return max(week1, week2, weeks3_4)


def compute_3week_lead_time_stock(fba_stock, rate):
	needed = flt(rate) * 3
	if flt(fba_stock) >= needed:
		return flt(fba_stock) - needed
	return 0


def compute_comment(asin, s3, s7, s14, s30):
	if not asin:
		return "Not Listed"
	if any([flt(s3) > 0, flt(s7) > 0, flt(s14) > 0, flt(s30) > 0]):
		return "Active"
	return "No Sales"


def compute_forecast(hwr, instocks, current, receiving, three_wk_lt):
	return flt(hwr) * 3 - flt(instocks) - flt(current) - flt(receiving) - flt(three_wk_lt)


def compute_ranking(sales_rank):
	rank = flt(sales_rank)
	if not rank:
		return "No Rank"
	if rank > 50000:
		return "High"
	return ""


def compute_sales_dropped(fba_stock, s7, hwr, prev_hwr):
	baseline = max(flt(hwr), flt(prev_hwr))
	if baseline <= 0 or flt(fba_stock) <= 0:
		return ""
	pct = (flt(s7) / baseline) - 1.0
	if pct <= -0.35:
		return "YES"
	return ""


def compute_program_forecast(
	buy_box_current, fba_stock, s3, s7, hwr, instocks, current, receiving, forecast, three_wk_lt, min_margin
):
	buy_box_current = flt(buy_box_current)
	fba_stock = flt(fba_stock)
	s3 = flt(s3)
	s7 = flt(s7)
	hwr = flt(hwr)
	instocks = flt(instocks)
	current = flt(current)
	receiving = flt(receiving)
	forecast = flt(forecast)
	three_wk_lt = flt(three_wk_lt)
	min_margin = flt(min_margin)

	if not buy_box_current:
		return ("", "No Buy Box price")

	sales7x4 = s7 * 4
	rate4x = hwr * 4
	total_buf = fba_stock + instocks + current + receiving

	if math.isclose(fba_stock, sales7x4, abs_tol=1e-6):
		return (0, "Enough FBA stock (≈7dX4)")

	if s3 == 0 and s7 == 0 and min_margin > 0.05 and forecast > 0:
		if three_wk_lt >= rate4x:
			return (0, "High lead-time (>=4Xwk rate), hold")
		return (forecast + hwr, "Zero recent sales but margin OK; send (forecast+hwr)")

	if forecast < 0 or (s3 == 0 and s7 == 0) or min_margin <= 0.05:
		if min_margin <= 0.05:
			return ("Margins", "Low margin")
		return (0, "Negative/zero conditions; hold")

	if fba_stock >= sales7x4:
		return (0, "AFN alone covers 4 weeks; hold")

	if total_buf >= sales7x4:
		if three_wk_lt == 0 or three_wk_lt < hwr:
			return (forecast + hwr, "Total buffer covers, lead OK; send (forecast+hwr)")
		return (0, "Buffer covers but lead high; hold")

	return (forecast + hwr, "Insufficient buffer; send (forecast+hwr)")


def compute_total_forecast(hwr, forecast):
	return flt(hwr) + flt(forecast)


def build_sku_info(selected_items):
	sku_names = [item.get("sku") for item in selected_items if item.get("sku")]
	if not sku_names:
		frappe.throw(_("No valid SKUs found in selection"))

	sku_rows = frappe.get_all(
		"Proxy SKU", filters={"name": ("in", sku_names)}, fields=["name", "item", "uom"]
	)
	return {row["name"]: {"item": row["item"], "uom": row["uom"]} for row in sku_rows}


def get_bundle_components_map(item_codes):
	"""Component rows for whichever of ``item_codes`` are bundle parents.

	Asking per item cost a query or two for every selected row, and this report is actioned in
	bulk — so the parents are found in one pass and only they are resolved.
	"""
	if not item_codes:
		return {}

	bundle_parents = frappe.get_all(
		"Product Bundle",
		filters={"new_item_code": ["in", item_codes]},
		pluck="new_item_code",
		distinct=True,
	)

	return {item_code: get_product_bundle_items(item_code) for item_code in bundle_parents}


def expand_selection(selected_items, sku_info):
	"""One row per stock item to move and to buy: a bundle SKU becomes its components, a plain SKU
	stays itself. Both the Material Request and the Purchase Order are built from this list, so the
	two cannot disagree about what a bundle contains.

	Component rows carry no UOM, so each lands in its own stock UOM — the same expansion ERPNext
	does when it builds a Packed Item from a bundle.
	"""
	unlinked_skus = []
	selections = []
	for item in selected_items:
		sku = item.get("sku")
		info = sku_info.get(sku)
		if not info or not info["item"]:
			unlinked_skus.append(sku)
			continue

		qty = flt(item.get("quantity_to_transfer") or 0)
		if qty > 0:
			selections.append((sku, info["item"], info["uom"], qty))

	if not selections:
		if unlinked_skus:
			frappe.throw(
				_("No Item is linked to the selected Proxy SKU(s): {0}").format(", ".join(unlinked_skus))
			)
		frappe.throw(_("No selected SKU has a Quantity to Transfer above zero"))

	if unlinked_skus:
		frappe.msgprint(
			_("Skipped — no Item is linked to these Proxy SKU(s): {0}").format(", ".join(unlinked_skus)),
			indicator="orange",
		)

	item_codes = list({selection[1] for selection in selections})
	components_map = get_bundle_components_map(item_codes)
	non_stock_items = set(
		frappe.get_all("Item", filters={"name": ["in", item_codes], "is_stock_item": 0}, pluck="name")
	)

	rows = []
	unbuildable_bundles = []
	for sku, item_code, uom, qty in selections:
		components = components_map.get(item_code)
		if not components:
			# A non-stock item with nothing to expand into cannot move stock. Letting it through
			# creates a transfer that looks fine and silently moves nothing.
			if item_code in non_stock_items:
				unbuildable_bundles.append(f"{sku} ({item_code})")
			else:
				rows.append({"sku": sku, "item_code": item_code, "qty": qty, "uom": uom})
			continue

		# Component qty is per one bundle in the bundle's stock UOM, so scale the selected qty to it.
		bundle_qty = qty * get_conversion_factor(item_code, uom)
		rows.extend(
			{
				"sku": sku,
				"item_code": component.item_code,
				"qty": bundle_qty * flt(component.qty),
				"uom": "",
			}
			for component in components
		)

	if unbuildable_bundles:
		frappe.throw(
			_(
				"No active Product Bundle for these non-stock SKUs, so there is nothing to transfer: {0}"
			).format(", ".join(unbuildable_bundles))
		)

	return rows


def get_shortfall_key(row):
	return f"{row['sku']}::{row['item_code']}"


def get_transfer_item_details(item_codes):
	if not item_codes:
		return {}

	rows = frappe.get_all(
		"Item", filters={"name": ["in", item_codes]}, fields=["name", "item_name", "stock_uom"]
	)
	return {row["name"]: row for row in rows}


def get_transferable_qty(free_stock_qty, conversion_factor, whole_units_only):
	qty = flt(free_stock_qty) / (conversion_factor or 1)
	if conversion_factor > 1 or whole_units_only:
		return float(math.floor(qty))
	return qty


def get_transfer_availability(item_codes, company, afn_warehouse):
	pickable_qty = get_pickable_stock_qty(item_codes, company, exclude_warehouses=[afn_warehouse])
	free_qty_map = get_free_qty_map(item_codes)

	return {
		item_code: min(
			flt(pickable_qty.get(item_code)),
			flt((free_qty_map.get(item_code) or {}).get("free_qty")),
		)
		for item_code in item_codes
	}


def resolve_transfer_shortfalls(transfer_rows, company, afn_warehouse, decisions=None):
	item_codes = list({row["item_code"] for row in transfer_rows})
	available = get_transfer_availability(item_codes, company, afn_warehouse)
	item_details = get_transfer_item_details(item_codes)
	whole_number_uoms = get_whole_number_uoms()

	kept_rows = []
	shortfalls = []
	for row in transfer_rows:
		item_code = row["item_code"]
		details = item_details.get(item_code) or {}
		conversion_factor = get_conversion_factor(item_code, row["uom"])
		requested_stock_qty = flt(row["qty"]) * conversion_factor
		free_stock_qty = max(flt(available.get(item_code)), 0.0)

		if free_stock_qty >= requested_stock_qty:
			available[item_code] = free_stock_qty - requested_stock_qty
			kept_rows.append(row)
			continue

		transferable_qty = get_transferable_qty(
			free_stock_qty, conversion_factor, details.get("stock_uom") in whole_number_uoms
		)
		key = get_shortfall_key(row)
		shortfalls.append(
			{
				"key": key,
				"sku": row["sku"],
				"item_code": item_code,
				"item_name": details.get("item_name") or item_code,
				"uom": row["uom"] or details.get("stock_uom") or "",
				"requested_qty": flt(row["qty"]),
				"available_qty": free_stock_qty,
				"transferable_qty": transferable_qty,
				"answered": decisions is not None and key in decisions,
			}
		)

		if decisions is None or key not in decisions:
			available[item_code] = 0.0
			kept_rows.append(row)
			continue

		if not decisions[key] or transferable_qty <= 0:
			continue

		available[item_code] = free_stock_qty - transferable_qty * conversion_factor
		kept_rows.append({**row, "qty": transferable_qty})

	return kept_rows, shortfalls


def get_shortfall_decisions(shortfall_decisions):
	if isinstance(shortfall_decisions, str):
		shortfall_decisions = frappe.parse_json(shortfall_decisions)

	return {key: bool(cint(value)) for key, value in (shortfall_decisions or {}).items()}


def apply_transfer_shortfall_decisions(transfer_rows, company, afn_warehouse, shortfall_decisions):
	kept_rows, shortfalls = resolve_transfer_shortfalls(
		transfer_rows, company, afn_warehouse, get_shortfall_decisions(shortfall_decisions)
	)

	unanswered_skus = [row["sku"] for row in shortfalls if not row["answered"]]
	if unanswered_skus:
		frappe.throw(
			_("Stock ran short for {0} since the quantities were checked, so nothing was created.").format(
				", ".join(sorted(set(unanswered_skus)))
			)
			+ " "
			+ _("Try again to see what can be sent now."),
			title=_("Stock Changed"),
		)

	if not kept_rows:
		frappe.throw(_("Every short item was left out, so there is nothing to transfer"))

	return kept_rows


def create_material_request_doc(transfer_rows, company, target_warehouse):
	material_request = frappe.new_doc("Material Request")
	material_request.material_request_type = "Material Transfer"
	material_request.company = company
	material_request.schedule_date = frappe.utils.today()

	for row in transfer_rows:
		material_request_item = {
			"item_code": row["item_code"],
			"qty": row["qty"],
			"schedule_date": frappe.utils.today(),
			"warehouse": target_warehouse,
		}
		if row["uom"]:
			material_request_item["uom"] = row["uom"]
		material_request.append("items", material_request_item)

	material_request.insert(ignore_permissions=True)
	material_request.submit()

	material_request_item_map = {}
	for row, material_request_row in zip(transfer_rows, material_request.items, strict=True):
		material_request_item_map.setdefault((row["sku"], row["item_code"]), []).append(
			material_request_row.name
		)

	return material_request, material_request_item_map


def plan_purchase_orders(transfer_rows):
	"""Group the expanded rows under the supplier ranked first for each item.

	The supplier is resolved here rather than read off the report row: a bundle SKU carries no
	supplier of its own, only its components do, so both halves have to come from one lookup.
	"""
	supplier_map = get_top_supplier_map(list({row["item_code"] for row in transfer_rows}))

	supplier_groups = {}
	unpurchasable_items = []
	for row in transfer_rows:
		item_code = row["item_code"]
		supplier_info = supplier_map.get(item_code)
		if not supplier_info or not supplier_info.get("supplier"):
			unpurchasable_items.append(item_code)
			continue

		supplier_uom = supplier_info.get("uom") or row["uom"]
		supplier_conversion_factor = get_conversion_factor(item_code, supplier_uom)
		qty_in_base = row["qty"] * get_conversion_factor(item_code, row["uom"])
		qty_in_supplier_uom = (
			ceil(qty_in_base / supplier_conversion_factor) if supplier_conversion_factor > 0 else qty_in_base
		)

		supplier_groups.setdefault(supplier_info["supplier"], []).append(
			{
				"sku": row["sku"],
				"item_code": item_code,
				"qty": qty_in_supplier_uom,
				"uom": supplier_uom,
				"conversion_factor": supplier_conversion_factor,
				"rate": flt(supplier_info.get("price")),
			}
		)

	unpurchasable_items = sorted(set(unpurchasable_items))
	if not supplier_groups:
		frappe.throw(
			_("No supplier is ranked first for any selected item, so there is nothing to order: {0}").format(
				", ".join(unpurchasable_items)
			)
		)

	if unpurchasable_items:
		frappe.msgprint(
			_("Left off the Purchase Order — no supplier ranked first: {0}").format(
				", ".join(unpurchasable_items)
			),
			indicator="orange",
		)

	return supplier_groups


def submit_purchase_orders(supplier_groups, company, warehouse):
	created_purchase_orders = []
	purchase_order_map = {}

	for supplier, items in supplier_groups.items():
		purchase_order = frappe.new_doc("Purchase Order")
		purchase_order.supplier = supplier
		purchase_order.company = company
		purchase_order.currency = frappe.db.get_default("currency")
		purchase_order.transaction_date = frappe.utils.today()
		purchase_order.schedule_date = frappe.utils.today()

		for item_data in items:
			purchase_order_item = {
				"item_code": item_data["item_code"],
				"qty": item_data["qty"],
				"conversion_factor": item_data["conversion_factor"],
				"rate": item_data["rate"],
				"schedule_date": frappe.utils.today(),
				"warehouse": warehouse,
			}
			if item_data["uom"]:
				purchase_order_item["uom"] = item_data["uom"]
			purchase_order.append("items", purchase_order_item)

		purchase_order.insert(ignore_permissions=True)
		purchase_order.submit()
		created_purchase_orders.append(purchase_order.name)

		for item_data in items:
			purchase_order_map[(item_data["sku"], item_data["item_code"])] = purchase_order.name

	return created_purchase_orders, purchase_order_map


@frappe.whitelist()
def create_purchase_orders_from_forecast_report(selected_items: str | list[dict]):
	if isinstance(selected_items, str):
		selected_items = frappe.parse_json(selected_items)

	if not selected_items:
		frappe.throw(_("No items selected"))

	sku_info = build_sku_info(selected_items)

	amz_setting = frappe.get_single("Amazon SP API Settings")
	if not amz_setting.warehouse:
		frappe.throw(_("Warehouse not configured in Amazon SP API Settings"))

	supplier_groups = plan_purchase_orders(expand_selection(selected_items, sku_info))
	created_purchase_orders = submit_purchase_orders(
		supplier_groups, amz_setting.company, amz_setting.warehouse
	)[0]

	return {
		"success": True,
		"purchase_orders": created_purchase_orders,
		"message": _("Created {0} Purchase Order(s)").format(len(created_purchase_orders)),
	}


@frappe.whitelist()
def get_transfer_stock_shortfalls(selected_items: str | list[dict]):
	if isinstance(selected_items, str):
		selected_items = frappe.parse_json(selected_items)

	if not selected_items:
		frappe.throw(_("No items selected"))

	amz_setting = frappe.get_single("Amazon SP API Settings")
	if not amz_setting.afn_warehouse:
		frappe.throw(_("AFN Warehouse not configured in Amazon SP API Settings"))

	transfer_rows = expand_selection(selected_items, build_sku_info(selected_items))

	return resolve_transfer_shortfalls(transfer_rows, amz_setting.company, amz_setting.afn_warehouse)[1]


@frappe.whitelist()
def create_material_requests_from_forecast_report(
	selected_items: str | list[dict], shortfall_decisions: str | dict | None = None
):
	if isinstance(selected_items, str):
		selected_items = frappe.parse_json(selected_items)

	if not selected_items:
		frappe.throw(_("No items selected"))

	sku_info = build_sku_info(selected_items)

	amz_setting = frappe.get_single("Amazon SP API Settings")
	company = amz_setting.company
	target_warehouse = amz_setting.afn_warehouse

	if not target_warehouse:
		frappe.throw(_("AFN Warehouse not configured in Amazon SP API Settings"))

	transfer_rows = apply_transfer_shortfall_decisions(
		expand_selection(selected_items, sku_info), company, target_warehouse, shortfall_decisions
	)

	material_request = create_material_request_doc(transfer_rows, company, target_warehouse)[0]

	return {
		"success": True,
		"material_request": material_request.name,
		"message": _("Created Material Request {0}").format(material_request.name),
	}


@frappe.whitelist()
def create_purchase_orders_and_material_request_from_forecast_report(
	selected_items: str, shortfall_decisions: str | dict | None = None
):
	if isinstance(selected_items, str):
		selected_items = frappe.parse_json(selected_items)

	if not selected_items:
		frappe.throw(_("No items selected"))

	sku_info = build_sku_info(selected_items)

	amz_setting = frappe.get_single("Amazon SP API Settings")
	company = amz_setting.company
	warehouse = amz_setting.warehouse
	target_warehouse = amz_setting.afn_warehouse

	if not target_warehouse:
		frappe.throw(_("AFN Warehouse not configured in Amazon SP API Settings"))

	if not warehouse:
		frappe.throw(_("Warehouse not configured in Amazon SP API Settings"))

	transfer_rows = apply_transfer_shortfall_decisions(
		expand_selection(selected_items, sku_info), company, target_warehouse, shortfall_decisions
	)

	# Resolve the orders before the transfer is submitted: a selection with nothing to buy has to
	# fail while there is still no submitted Material Request to strand.
	supplier_groups = plan_purchase_orders(transfer_rows)

	material_request, material_request_item_map = create_material_request_doc(
		transfer_rows, company, target_warehouse
	)
	created_purchase_orders, purchase_order_map = submit_purchase_orders(supplier_groups, company, warehouse)

	# Keyed per component, not per SKU: the FBA Transfer Tracker joins a transfer row to its order
	# on (purchase order, item code), so a bundle's components each need their own link.
	for key, material_request_item_names in material_request_item_map.items():
		purchase_order_name = purchase_order_map.get(key)
		if not purchase_order_name:
			continue

		for material_request_item_name in material_request_item_names:
			frappe.db.set_value(
				"Material Request Item",
				material_request_item_name,
				"custom_purchase_order",
				purchase_order_name,
			)

	return {
		"success": True,
		"material_request": material_request.name,
		"purchase_orders": created_purchase_orders,
		"message": _("Created Material Request {0} and {1} Purchase Order(s)").format(
			material_request.name, len(created_purchase_orders)
		),
	}
