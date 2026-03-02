# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import csv
import io
import re

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count, Sum
from frappe.utils import flt

from navgold_custom.navgold_custom.utils import get_free_qty_map


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
	]


def get_data(filters):
	asset = frappe.get_cached_doc("Navgold Forecast Asset").as_dict()

	fba_map = _parse_fba_inventory(asset.get("fba_inventory_csv"))
	sales_3d_map = _parse_sales_csv(asset.get("sales_3d_csv"))
	sales_7d_map = _parse_sales_csv(asset.get("sales_7d_csv"))
	sales_14d_map = _parse_sales_csv(asset.get("sales_14d_csv"))
	sales_30d_map = _parse_sales_csv(asset.get("sales_30d_csv"))
	revenue_map = _parse_30d_revenue(asset.get("sales_30d_csv"))
	keepa_map = _parse_keepa(asset.get("keepa_xlsx"))
	your_price_map = _parse_your_price(asset.get("your_price_xlsx"))
	prev_rate_map = _parse_previous_report(asset.get("previous_report_xlsx"))

	proxy_sku_map = _get_proxy_sku_map()
	if not proxy_sku_map:
		return []

	item_codes = list({v["item"] for v in proxy_sku_map.values() if v.get("item")})
	proxy_sku_names = list(proxy_sku_map.keys())

	item_info_map = _get_item_info_map(item_codes)
	variant_mult_map = _get_variant_multiplier_map(proxy_sku_map)
	free_qty_map = get_free_qty_map(item_codes) if item_codes else {}
	supplier_map = _get_top_supplier_map(item_codes)
	pricing_map = _get_pricing_map(proxy_sku_names)
	po_map = _get_po_data_map(item_codes)
	channel_sales_map = _get_sales_by_channel(item_codes)
	instocks_map = _get_instocks_map(item_codes)
	current_map = _get_current_picking_map(item_codes)

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
		po = po_map.get(item_code, {})
		free_stock = flt(free_qty_map.get(item_code, {}).get("free_qty", 0))

		fba_stock = flt(fba.get("available", 0))
		fnsku = fba.get("fnsku", "")
		receiving = flt(fba.get("receiving", 0))

		channel_sales = channel_sales_map.get(item_code, {})
		fba_sales = flt(channel_sales.get("fba_sales", 0))
		sfp_sales = flt(channel_sales.get("sfp_sales", 0))
		total_sales = fba_sales + sfp_sales

		instocks = flt(instocks_map.get(item_code, 0))
		current = flt(current_map.get(item_code, 0))

		current_month_offer = supplier.get("current_month_offer", "")
		next_month_offer = supplier.get("next_month_offer", "")

		highest_weekly_rate = _compute_highest_weekly_rate(s7, s14, s30)
		three_week_lt = _compute_3week_lead_time_stock(fba_stock, highest_weekly_rate)
		strategy = "FBA" if fba_stock > 0 else "SFP"
		comment = _compute_comment(asin, s3, s7, s14, s30)
		four_week_forecast = highest_weekly_rate * 4

		forecast = _compute_forecast(highest_weekly_rate, instocks, current, receiving, three_week_lt)
		ranking = _compute_ranking(keepa.get("sales_rank", 0))
		sales_dropped = _compute_sales_dropped(fba_stock, s7, highest_weekly_rate, prev_rate)
		program_forecast, program_reason = _compute_program_forecast(
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
		total_forecast = _compute_total_forecast(highest_weekly_rate, forecast)

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
			"po_count": po.get("po_count", 0),
			"total_qty_on_order": flt(po.get("total_qty", 0)),
			"comment": comment,
		}
		data.append(row)

	if filters.get("brand"):
		data = [r for r in data if r["brand"] == filters["brand"]]
	if filters.get("strategy"):
		data = [r for r in data if r["strategy"] == filters["strategy"]]
	if filters.get("comment"):
		data = [r for r in data if r["comment"] == filters["comment"]]

	return data


# def get_forecast_asset():
# 	try:
# 		return
# 	except frappe.DoesNotExistError:
# 		return {}


def _read_file_content(file_url):
	"""Read an attached file and return bytes content."""
	if not file_url:
		return None
	try:
		_file = frappe.get_doc("File", {"file_url": file_url})
		return _file.get_content()
	except Exception:
		frappe.log_error(f"Failed to read file: {file_url}", "Navgold Forecast Report")
		return None


def _parse_fba_inventory(file_url):
	"""Parse FBA Inventory CSV (comma-separated). Returns {sku: {available, fnsku, receiving}}."""
	content = _read_file_content(file_url)
	if not content:
		return {}

	if isinstance(content, bytes):
		content = content.decode("utf-8-sig", errors="replace")

	result = {}
	reader = csv.DictReader(io.StringIO(content))
	for row in reader:
		sku = (row.get("sku") or row.get("SKU") or "").strip()
		if not sku:
			continue
		result[sku] = {
			"available": flt(row.get("available", 0)),
			"fnsku": (row.get("fnsku") or row.get("FNSKU") or "").strip(),
			"receiving": (
				flt(row.get("inbound-working", 0))
				+ flt(row.get("inbound-shipped", 0))
				+ flt(row.get("inbound-received", 0))
			),
		}
	return result


def _parse_sales_csv(file_url):
	"""Parse Amazon Sales CSV (comma-separated). Returns {sku: {units: float}}."""
	content = _read_file_content(file_url)
	if not content:
		return {}

	if isinstance(content, bytes):
		content = content.decode("utf-8-sig", errors="replace")

	result = {}
	reader = csv.DictReader(io.StringIO(content))
	for row in reader:
		sku = (row.get("SKU") or row.get("sku") or "").strip()
		if not sku or sku.lower() == "sku":
			continue
		units = flt(_strip_currency(row.get("Units ordered", 0)))
		result[sku] = {"units": units}
	return result


def _parse_30d_revenue(file_url):
	"""Parse 30D Sales CSV for revenue (comma-separated). Returns {sku: {revenue: float}}."""
	content = _read_file_content(file_url)
	if not content:
		return {}

	if isinstance(content, bytes):
		content = content.decode("utf-8-sig", errors="replace")

	result = {}
	reader = csv.DictReader(io.StringIO(content))
	for row in reader:
		sku = (row.get("SKU") or row.get("sku") or "").strip()
		if not sku or sku.lower() == "sku":
			continue
		revenue = flt(_strip_currency(row.get("Ordered Product Sales", 0)))
		result[sku] = {"revenue": revenue}
	return result


def _parse_keepa(file_url):
	"""Parse Keepa XLSX. Returns {asin: {buy_box: float, sales_rank: int}}."""
	content = _read_file_content(file_url)
	if not content:
		return {}

	try:
		import openpyxl

		wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
		ws = wb.active

		headers = []
		result = {}
		asin_col = None
		buy_box_col = None
		sales_rank_col = None

		for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
			if row_idx == 0:
				headers = [str(c or "").strip().lower() for c in row]
				for i, h in enumerate(headers):
					if h == "asin":
						asin_col = i
					elif "buy box" in h and "current" in h:
						buy_box_col = i
					elif "sales rank" in h and "current" in h:
						sales_rank_col = i
				continue

			if asin_col is None:
				break

			asin = str(row[asin_col] or "").strip() if row[asin_col] else ""
			if not asin:
				continue

			buy_box = flt(row[buy_box_col]) if buy_box_col is not None and row[buy_box_col] else 0
			sales_rank = (
				int(flt(row[sales_rank_col])) if sales_rank_col is not None and row[sales_rank_col] else 0
			)

			result[asin] = {"buy_box": buy_box, "sales_rank": sales_rank}

		wb.close()
		return result
	except Exception:
		frappe.log_error(f"Failed to parse Keepa XLSX: {file_url}", "Navgold Forecast Report")
		return {}


def _parse_your_price(file_url):
	"""Parse Your Price XLSX. Returns {sku: {price: float}}."""
	content = _read_file_content(file_url)
	if not content:
		return {}

	try:
		import openpyxl

		wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
		ws = wb.active

		headers = []
		result = {}
		sku_col = None
		price_col = None

		for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
			if row_idx == 0:
				headers = [str(c or "").strip().lower() for c in row]
				for i, h in enumerate(headers):
					if h in ("seller-sku", "sku", "seller sku"):
						sku_col = i
					elif h in ("price", "your-price", "your price"):
						price_col = i
				continue

			if sku_col is None:
				break

			sku = str(row[sku_col] or "").strip() if row[sku_col] else ""
			if not sku:
				continue

			price = flt(row[price_col]) if price_col is not None and row[price_col] else 0
			result[sku] = {"price": price}

		wb.close()
		return result
	except Exception:
		frappe.log_error(f"Failed to parse Your Price XLSX: {file_url}", "Navgold Forecast Report")
		return {}


def _parse_previous_report(file_url):
	"""Parse Previous Report XLSX. Returns {sku: {highest_weekly_rate: float}}."""
	content = _read_file_content(file_url)
	if not content:
		return {}

	try:
		import openpyxl

		wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
		ws = wb.active

		headers = []
		result = {}
		sku_col = None
		rate_col = None

		for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
			if row_idx == 0:
				headers = [str(c or "").strip().lower() for c in row]
				for i, h in enumerate(headers):
					if h in ("sku", "seller_sku"):
						sku_col = i
					elif "highest" in h and "weekly" in h and "rate" in h:
						rate_col = i
				continue

			if sku_col is None:
				break

			sku = str(row[sku_col] or "").strip() if row[sku_col] else ""
			if not sku:
				continue

			rate = flt(row[rate_col]) if rate_col is not None and row[rate_col] else 0
			result[sku] = {"highest_weekly_rate": rate}

		wb.close()
		return result
	except Exception:
		frappe.log_error(f"Failed to parse Previous Report XLSX: {file_url}", "Navgold Forecast Report")
		return {}


def _strip_currency(val):
	"""Strip currency symbols (£, $) and commas from a value string."""
	if not val:
		return 0
	if isinstance(val, int | float):
		return val
	return re.sub(r"[£$,]", "", str(val).strip())


def _get_proxy_sku_map():
	"""Returns {sku_name: {item, asin, uom, is_fba}}."""
	ps = DocType("Proxy SKU")
	rows = (
		frappe.qb.from_(ps).select(ps.name, ps.item, ps.asin, ps.uom, ps.is_fba).where(ps.is_active == 1)
	).run(as_dict=True)

	return {
		r["name"]: {
			"item": r["item"],
			"asin": r["asin"] or "",
			"uom": r["uom"],
			"is_fba": r["is_fba"],
		}
		for r in rows
	}


def _get_item_info_map(item_codes):
	"""Returns {item_code: {brand, item_name}}."""
	if not item_codes:
		return {}

	item = DocType("Item")
	rows = (
		frappe.qb.from_(item).select(item.name, item.brand, item.item_name).where(item.name.isin(item_codes))
	).run(as_dict=True)

	return {r["name"]: {"brand": r["brand"] or "", "item_name": r["item_name"] or ""} for r in rows}


def _get_sales_by_channel(item_codes):
	"""Returns {item_code: {fba_sales: float, sfp_sales: float}} from submitted Sales Orders."""
	if not item_codes:
		return {}

	from frappe.utils import add_days, today

	so = DocType("Sales Order")
	soi = DocType("Sales Order Item")
	thirty_days_ago = add_days(today(), -30)

	rows = (
		frappe.qb.from_(so)
		.join(soi)
		.on(soi.parent == so.name)
		.select(soi.item_code, so.fulfillment_channel, Sum(soi.qty).as_("total_qty"))
		.where(
			(so.docstatus == 1)
			& (so.transaction_date >= thirty_days_ago)
			& (so.fulfillment_channel.isin(["AFN", "MFN"]))
			& (soi.item_code.isin(item_codes))
		)
		.groupby(soi.item_code, so.fulfillment_channel)
	).run(as_dict=True)

	result = {}
	for r in rows:
		item = r["item_code"]
		if item not in result:
			result[item] = {"fba_sales": 0, "sfp_sales": 0}
		if r["fulfillment_channel"] == "AFN":
			result[item]["fba_sales"] = flt(r["total_qty"])
		elif r["fulfillment_channel"] == "MFN":
			result[item]["sfp_sales"] = flt(r["total_qty"])
	return result


def _get_variant_multiplier_map(proxy_sku_map):
	"""Returns {sku: conversion_factor} from UOM Conversion Detail."""
	if not proxy_sku_map:
		return {}

	uom_detail = DocType("UOM Conversion Detail")

	# Build list of (item, uom) pairs we need
	pairs = [(v["item"], v["uom"]) for v in proxy_sku_map.values() if v.get("item") and v.get("uom")]
	if not pairs:
		return {}

	item_codes = list({p[0] for p in pairs})

	rows = (
		frappe.qb.from_(uom_detail)
		.select(uom_detail.parent, uom_detail.uom, uom_detail.conversion_factor)
		.where((uom_detail.parenttype == "Item") & (uom_detail.parent.isin(item_codes)))
	).run(as_dict=True)

	# Build lookup by (item, uom)
	uom_lookup = {}
	for r in rows:
		uom_lookup[(r["parent"], r["uom"])] = flt(r["conversion_factor"])

	# Map back to SKU names
	result = {}
	for sku, info in proxy_sku_map.items():
		key = (info.get("item"), info.get("uom"))
		if key in uom_lookup:
			result[sku] = uom_lookup[key]

	return result


def _get_top_supplier_map(item_codes):
	"""Returns {item_code: {supplier, price, current_month_offer, next_month_offer}} where rank=1."""
	if not item_codes:
		return {}

	sips = DocType("Supplier Item Price and Stock")
	rows = (
		frappe.qb.from_(sips)
		.select(sips.item, sips.supplier, sips.price)
		.where((sips.item.isin(item_codes)) & (sips.rank == 1))
	).run(as_dict=True)

	result = {r["item"]: {"supplier": r["supplier"], "price": flt(r["price"])} for r in rows}

	# Fetch offers for rank-1 suppliers (idx=1 → current month, idx=2 → next month)
	so = DocType("Supplier Offer")
	offer_rows = (
		frappe.qb.from_(sips)
		.join(so)
		.on(so.parent == sips.name)
		.select(sips.item, so.offer, so.idx)
		.where((sips.item.isin(item_codes)) & (sips.rank == 1))
		.orderby(so.idx)
	).run(as_dict=True)

	for r in offer_rows:
		item = r["item"]
		if item not in result:
			continue
		if r["idx"] == 1:
			result[item]["current_month_offer"] = r["offer"] or ""
		elif r["idx"] == 2:
			result[item]["next_month_offer"] = r["offer"] or ""

	return result


def _get_instocks_map(item_codes):
	"""Returns {item_code: outstanding_qty} from open Purchase Orders."""
	if not item_codes:
		return {}

	po = DocType("Purchase Order")
	poi = DocType("Purchase Order Item")

	rows = (
		frappe.qb.from_(po)
		.join(poi)
		.on(poi.parent == po.name)
		.select(poi.item_code, Sum(poi.qty - poi.received_qty).as_("outstanding_qty"))
		.where(
			(po.docstatus == 1)
			& (po.status.notin(["Completed", "Cancelled", "Closed"]))
			& (poi.item_code.isin(item_codes))
		)
		.groupby(poi.item_code)
	).run(as_dict=True)

	return {r["item_code"]: flt(r["outstanding_qty"]) for r in rows}


def _get_current_picking_map(item_codes):
	"""Returns {item_code: qty} from open Pick Lists (Material Transfer purpose)."""
	if not item_codes:
		return {}

	pl = DocType("Pick List")
	pli = DocType("Pick List Item")

	rows = (
		frappe.qb.from_(pl)
		.join(pli)
		.on(pli.parent == pl.name)
		.select(pli.item_code, Sum(pli.qty).as_("total_qty"))
		.where(
			(pl.docstatus == 1)
			& (pl.purpose == "Material Transfer")
			& (pl.status.isin(["Open", "Partly Delivered"]))
			& (pli.item_code.isin(item_codes))
		)
		.groupby(pli.item_code)
	).run(as_dict=True)

	return {r["item_code"]: flt(r["total_qty"]) for r in rows}


def _get_pricing_map(proxy_sku_names):
	"""Returns {proxy_sku: {min_price, min_margin}} from Amazon Product Pricing + Strategy."""
	if not proxy_sku_names:
		return {}

	app = DocType("Amazon Product Pricing")
	aps = DocType("Amazon Product Strategy")

	rows = (
		frappe.qb.from_(app)
		.left_join(aps)
		.on(app.active_strategy == aps.name)
		.select(
			app.proxy_sku,
			app.current_min_price,
			aps.min_margin,
		)
		.where((app.proxy_sku.isin(proxy_sku_names)) & (app.is_active == 1))
	).run(as_dict=True)

	return {
		r["proxy_sku"]: {
			"min_price": flt(r["current_min_price"]),
			"min_margin": flt(r["min_margin"]),
		}
		for r in rows
	}


def _get_po_data_map(item_codes):
	"""Returns {item_code: {po_count, total_qty}} from open Purchase Orders."""
	if not item_codes:
		return {}

	po = DocType("Purchase Order")
	poi = DocType("Purchase Order Item")

	rows = (
		frappe.qb.from_(po)
		.join(poi)
		.on(poi.parent == po.name)
		.select(
			poi.item_code,
			Count(po.name).as_("po_count"),
			Sum(poi.qty).as_("total_qty"),
		)
		.where(
			(po.docstatus == 1)
			& (po.status.notin(["Completed", "Cancelled", "Closed"]))
			& (poi.item_code.isin(item_codes))
		)
		.groupby(poi.item_code)
	).run(as_dict=True)

	return {
		r["item_code"]: {
			"po_count": r["po_count"] or 0,
			"total_qty": flt(r["total_qty"]),
		}
		for r in rows
	}


def _compute_highest_weekly_rate(s7, s14, s30):
	"""
	Compute highest weekly sales rate from multi-period data.
	max(s7, max(0, s14-s7), max(0, s30-s14)/2)
	"""
	week1 = flt(s7)
	week2 = max(0, flt(s14) - flt(s7))
	weeks3_4 = max(0, flt(s30) - flt(s14)) / 2
	return max(week1, week2, weeks3_4)


def _compute_3week_lead_time_stock(fba_stock, rate):
	"""Stock remaining after 3 weeks of sales at the given rate."""
	needed = flt(rate) * 3
	if flt(fba_stock) >= needed:
		return flt(fba_stock) - needed
	return 0


def _compute_comment(asin, s3, s7, s14, s30):
	"""Determine SKU status: Not Listed, Active, or No Sales."""
	if not asin:
		return "Not Listed"
	if any([flt(s3) > 0, flt(s7) > 0, flt(s14) > 0, flt(s30) > 0]):
		return "Active"
	return "No Sales"


def _compute_forecast(hwr, instocks, current, receiving, three_wk_lt):
	"""forecast = (hwr * 3) - instocks - current - receiving - three_wk_lt"""
	return flt(hwr) * 3 - flt(instocks) - flt(current) - flt(receiving) - flt(three_wk_lt)


def _compute_ranking(sales_rank):
	"""'No Rank' if blank/0; 'High' if >50000; else ''."""
	rank = flt(sales_rank)
	if not rank:
		return "No Rank"
	if rank > 50000:
		return "High"
	return ""


def _compute_sales_dropped(fba_stock, s7, hwr, prev_hwr):
	"""YES if fba_stock > 0 and 7d sales >= 35% below baseline."""
	baseline = max(flt(hwr), flt(prev_hwr))
	if baseline <= 0 or flt(fba_stock) <= 0:
		return ""
	pct = (flt(s7) / baseline) - 1.0
	if pct <= -0.35:
		return "YES"
	return ""


def _compute_program_forecast(
	buy_box_current, fba_stock, s3, s7, hwr, instocks, current, receiving, forecast, three_wk_lt, min_margin
):
	import math

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

	# If no buy box price → blank
	if not buy_box_current:
		return ("", "No Buy Box price")

	sales7x4 = s7 * 4
	rate4x = hwr * 4
	total_buf = fba_stock + instocks + current + receiving

	# Exact FBA 4-week coverage
	if math.isclose(fba_stock, sales7x4, abs_tol=1e-6):
		return (0, "Enough FBA stock (≈7dX4)")

	# Rule A: No recent sales but margin OK and positive forecast
	if s3 == 0 and s7 == 0 and min_margin > 0.05 and forecast > 0:
		if three_wk_lt >= rate4x:
			return (0, "High lead-time (>=4Xwk rate), hold")
		return (forecast + hwr, "Zero recent sales but margin OK; send (forecast+hwr)")

	# Rule B: Negative forecast, or no sales, or low margin
	if forecast < 0 or (s3 == 0 and s7 == 0) or min_margin <= 0.05:
		if min_margin <= 0.05:
			return ("Margins", "Low margin")
		return (0, "Negative/zero conditions; hold")

	# Rule C: Has sales, positive forecast, margin OK
	if fba_stock >= sales7x4:
		return (0, "AFN alone covers 4 weeks; hold")

	if total_buf >= sales7x4:
		if three_wk_lt == 0 or three_wk_lt < hwr:
			return (forecast + hwr, "Total buffer covers, lead OK; send (forecast+hwr)")
		return (0, "Buffer covers but lead high; hold")

	# Insufficient buffer
	return (forecast + hwr, "Insufficient buffer; send (forecast+hwr)")


def _compute_total_forecast(hwr, forecast):
	"""total_forecast = hwr + forecast"""
	return flt(hwr) + flt(forecast)
