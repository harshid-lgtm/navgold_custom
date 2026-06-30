# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import math
from functools import partial

import frappe
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count
from frappe.utils import add_days, fmt_money, getdate, now_datetime, today
from frappe.utils.caching import redis_cache
from frappe.utils.data import cint, cstr, flt, orjson_dumps

from navgold_custom.navgold_custom.amazon_pricing.price_calculator import (
	calculate_prices_with_purchase_cost_override,
)
from navgold_custom.navgold_custom.amazon_pricing.price_guard import (
	partition_pricing_by_price_deviation,
)
from navgold_custom.navgold_custom.amazon_pricing.pricing_validators import (
	collect_pricing_violations,
)
from navgold_custom.navgold_custom.amazon_pricing.purchase_cost_sources import (
	calculate_bundle_cost,
	get_buildable_bundle_qty_map,
)
from navgold_custom.navgold_custom.amazon_pricing.strategy_selector import (
	get_stock_info,
	get_stock_info_batch,
)
from navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku import get_proxy_sku_map
from navgold_custom.navgold_custom.doctype.supplier_item_price_and_stock.supplier_item_price_and_stock import (
	get_supplier_prices,
)
from navgold_custom.navgold_custom.erpnext_compat import get_product_bundle_items
from navgold_custom.navgold_custom.utils import get_conversion_factor

SHIPPING_GROUPS = {
	"sfp": "57d54587-7341-4c9f-b3f9-546c8d798b21",
	"main_revised": "a31ca016-36ab-481f-b399-b61d727c93d3",
	"two_day": "85513b3c-c531-42c1-9adb-ee55d0318d1a",
}


def get_top_supplier_for_item(item_code: str) -> dict | None:
	suppliers = frappe.get_all(
		"Supplier Item Price and Stock",
		filters={"item": item_code, "purchase_blocked": 0},
		fields=["supplier", "stock_qty", "rank", "price"],
		order_by="rank asc",
		limit=1,
	)
	return suppliers[0] if suppliers else None


def get_best_in_stock_supplier_for_item(item_code: str) -> dict | None:
	suppliers = frappe.get_all(
		"Supplier Item Price and Stock",
		filters={"item": item_code, "purchase_blocked": 0},
		fields=["supplier", "stock_qty", "rank", "price"],
		order_by="rank asc",
	)
	if not suppliers:
		return None

	for supplier in suppliers:
		if flt(supplier.get("stock_qty", 0)) > 0:
			return supplier

	return suppliers[0]


# This is for swapping backorder strategy prices based on supplier stock
def recompute_backorder_prices_with_fallback_supplier(
	pricing,
	resolved_suppliers: dict | None = None,
	backorder_strategy_map: dict | None = None,
) -> dict | None:
	if not pricing.get("backorder_strategy"):
		return None
	if pricing.get("active_strategy") != pricing.get("backorder_strategy"):
		return None

	proxy_sku_info = get_proxy_sku_map(sku_names=[pricing["proxy_sku"]]).get(pricing["proxy_sku"])
	if not proxy_sku_info or not proxy_sku_info.get("item"):
		return None
	item_code = proxy_sku_info["item"]
	proxy_sku_uom = proxy_sku_info["uom"]

	bundle_components = get_product_bundle_items(item_code)
	if bundle_components:
		fulfillable_cost = calculate_bundle_cost(item_code, bundle_components, get_supplier_prices)
		if fulfillable_cost <= 0:
			frappe.log_error(
				f"Bundle {item_code}: a component has no in-stock supplier; skipping fallback repricing",
				"Amazon Feed Builder",
			)
			return None

		cheapest_cost = calculate_bundle_cost(
			item_code, bundle_components, partial(get_supplier_prices, require_stock=False)
		)
		if fulfillable_cost <= cheapest_cost:
			return None

		override_price = fulfillable_cost
		rank_one_supplier = f"bundle ({len(bundle_components)} components)"
		rank_one_price = cheapest_cost
		fallback_supplier = "in-stock components"
		fallback_supplier_price = fulfillable_cost
	else:
		if resolved_suppliers:
			top_supplier = resolved_suppliers.get("top_supplier")
			in_stock_supplier = resolved_suppliers.get("in_stock_supplier")
		else:
			top_supplier = get_top_supplier_for_item(item_code)
			in_stock_supplier = get_best_in_stock_supplier_for_item(item_code)

		if not top_supplier or not in_stock_supplier:
			return None
		if in_stock_supplier.supplier == top_supplier.supplier:
			return None
		if flt(in_stock_supplier.get("stock_qty", 0)) <= 0:
			return None

		override_price = flt(in_stock_supplier.price) * get_conversion_factor(item_code, proxy_sku_uom)
		rank_one_supplier = top_supplier.supplier
		rank_one_price = flt(top_supplier.price)
		fallback_supplier = in_stock_supplier.supplier
		fallback_supplier_price = flt(in_stock_supplier.price)

	if backorder_strategy_map is not None:
		backorder_strategy = backorder_strategy_map.get(pricing["backorder_strategy"])
		if not backorder_strategy:
			return None
	else:
		try:
			backorder_strategy = frappe.get_cached_doc(
				"Amazon Product Strategy", pricing["backorder_strategy"]
			)
		except frappe.DoesNotExistError:
			return None

	recomputed = calculate_prices_with_purchase_cost_override(backorder_strategy, override_price)
	recomputed_min = flt(recomputed.get("min_price", 0))
	recomputed_max = flt(recomputed.get("max_price", 0))
	if recomputed_min <= 0 or recomputed_max <= 0:
		return None

	return {
		"final_min_price": recomputed_min,
		"final_max_price": recomputed_max,
		"fallback_repricing": {
			"proxy_sku": pricing["proxy_sku"],
			"rank_one_supplier": rank_one_supplier,
			"rank_one_price": rank_one_price,
			"fallback_supplier": fallback_supplier,
			"fallback_supplier_price": fallback_supplier_price,
			"original_min_price": flt(pricing.get("current_min_price", 0)),
			"original_max_price": flt(pricing.get("current_max_price", 0)),
			"recomputed_min_price": recomputed_min,
			"recomputed_max_price": recomputed_max,
		},
	}


def format_supplier_fallback_notes(fallback_repricings: list) -> str:
	def money(amount: float) -> str:
		return fmt_money(amount, currency="GBP")

	lines = []
	for record in fallback_repricings:
		lines.append(
			f"{record['proxy_sku']}: rank-1 {record['rank_one_supplier']} "
			f"({money(record['rank_one_price'])}, out of stock) -> "
			f"{record['fallback_supplier']} ({money(record['fallback_supplier_price'])}); "
			f"price min {money(record['original_min_price'])}->{money(record['recomputed_min_price'])}, "
			f"max {money(record['original_max_price'])}->{money(record['recomputed_max_price'])}"
		)
	return "\n".join(lines)


def get_supplier_lead_time(supplier_name: str) -> int:
	current_datetime = now_datetime()
	day = current_datetime.strftime("%A")
	row = frappe.db.get_value(
		"Supplier Lead Time",
		{"parent": supplier_name, "parenttype": "Supplier", "day": day},
		["lead_time_before_12", "lead_time_after_12"],
		as_dict=True,
	)
	if not row:
		return 1

	lead_time = row.lead_time_before_12 if current_datetime.hour < 12 else row.lead_time_after_12
	return cint(lead_time) or 1


def get_supplier_data_batch(item_codes: list) -> dict:
	if not item_codes:
		return {}

	all_suppliers = frappe.get_all(
		"Supplier Item Price and Stock",
		filters={"item": ["in", item_codes], "purchase_blocked": 0},
		fields=["supplier", "stock_qty", "rank", "price", "item"],
		order_by="item asc, rank asc",
	)

	suppliers_by_item: dict = {}
	for row in all_suppliers:
		suppliers_by_item.setdefault(row.item, []).append(row)

	result = {}
	for item_code in item_codes:
		item_suppliers = suppliers_by_item.get(item_code, [])
		top_supplier = item_suppliers[0] if item_suppliers else None
		in_stock_supplier = next(
			(supplier for supplier in item_suppliers if flt(supplier.get("stock_qty", 0)) > 0),
			top_supplier,
		)
		result[item_code] = {
			"top_supplier": top_supplier,
			"in_stock_supplier": in_stock_supplier,
		}

	return result


def get_lead_time_batch(supplier_names: list) -> dict:
	if not supplier_names:
		return {}

	current_datetime = now_datetime()
	day = current_datetime.strftime("%A")
	is_before_noon = current_datetime.hour < 12

	rows = frappe.get_all(
		"Supplier Lead Time",
		filters={"parent": ["in", supplier_names], "parenttype": "Supplier", "day": day},
		fields=["parent", "lead_time_before_12", "lead_time_after_12"],
	)

	return {
		row.parent: cint(row.lead_time_before_12 if is_before_noon else row.lead_time_after_12) or 1
		for row in rows
	}


def get_backorder_strategy_map(strategy_names: list) -> dict:
	if not strategy_names:
		return {}

	parent_rows = frappe.get_all(
		"Amazon Product Strategy",
		filters={"name": ["in", strategy_names]},
		fields=["name", "min_margin", "max_margin"],
	)

	cost_rows = frappe.get_all(
		"Amazon Product Cost",
		filters={
			"parent": ["in", strategy_names],
			"parenttype": "Amazon Product Strategy",
			"is_active": 1,
		},
		fields=["parent", "is_active", "cost_type", "value"],
		order_by="idx asc",
	)

	costs_by_strategy: dict = {}
	for cost in cost_rows:
		costs_by_strategy.setdefault(cost.parent, []).append(cost)

	return {
		row.name: frappe._dict(
			name=row.name,
			min_margin=row.min_margin,
			max_margin=row.max_margin,
			costs=costs_by_strategy.get(row.name, []),
		)
		for row in parent_rows
	}


# This is a bit complicated
# You have a Proxy SKU which maps to an Item and UOM. The Item can have multiple active Proxy SKUs (pool), and the UOM has a conversion factor (pack size). So we divide the stock into all the active Proxy Skus
def build_proxy_sku_info_map(proxy_sku_names: list) -> dict:
	proxy_sku_rows = frappe.get_all(
		"Proxy SKU",
		filters={"name": ["in", proxy_sku_names]},
		fields=["name", "item", "uom", "merchant_shipping_group"],
	)

	proxy_sku_map = {row.name: row for row in proxy_sku_rows}

	item_codes = list({row.item for row in proxy_sku_rows if row.item})
	proxy_sku_dt = DocType("Proxy SKU")
	pool_counts = (
		frappe.qb.from_(proxy_sku_dt)
		.select(proxy_sku_dt.item, Count("*").as_("pool_size"))
		.where((proxy_sku_dt.item.isin(item_codes)) & (proxy_sku_dt.is_active == 1))
		.groupby(proxy_sku_dt.item)
	).run(as_dict=True)
	pool_size_map = {row.item: row.pool_size for row in pool_counts}

	# UOM conversion factors: one batch query
	uom_conversion_dt = DocType("UOM Conversion Detail")
	needed_uom_pairs = list({(row.item, row.uom) for row in proxy_sku_rows if row.item and row.uom})
	if needed_uom_pairs:
		uom_factors = (
			frappe.qb.from_(uom_conversion_dt)
			.select(uom_conversion_dt.parent, uom_conversion_dt.uom, uom_conversion_dt.conversion_factor)
			.where((uom_conversion_dt.parenttype == "Item") & (uom_conversion_dt.parent.isin(item_codes)))
		).run(as_dict=True)
		uom_factor_map = {(row.parent, row.uom): flt(row.conversion_factor) or 1.0 for row in uom_factors}
	else:
		uom_factor_map = {}

	result = {}
	for proxy_sku, row in proxy_sku_map.items():
		pool_size = pool_size_map.get(row.item, 1)
		pack_size = uom_factor_map.get((row.item, row.uom), 1.0)
		result[proxy_sku] = {
			"item": row.item,
			"uom": row.uom,
			"pool_size": pool_size,
			"pack_size": pack_size,
			"merchant_shipping_group": row.merchant_shipping_group,
		}

	return result


def calculate_adjusted_qty(raw_qty: float, pool_size: int, pack_size: float) -> int:
	if pool_size <= 0 or pack_size <= 0:
		return 0
	return max(math.floor(raw_qty / pool_size / pack_size), 0)


def apply_stock_guard(qty: int, reserve_pct: float, max_publish_qty: int) -> int:
	# Clamp defensively: a negative reserve would inflate the published qty and oversell on Amazon,
	# and form-level non_negative won't stop a console/API/db_set write. >100 falls out as publish-nothing.
	reserve_pct = max(0.0, min(flt(reserve_pct), 100.0))
	max_publish_qty = cint(max_publish_qty)
	guarded_qty = max(math.floor(flt(qty) * (1 - reserve_pct / 100)), 0)
	if max_publish_qty > 0:
		guarded_qty = min(guarded_qty, max_publish_qty)
	return guarded_qty


def get_sku_inventory_data(
	pricing: dict,
	zero_stock_proxy_skus: set | None = None,
	proxy_sku_info: dict | None = None,
	stock_info_map: dict | None = None,
	supplier_data_map: dict | None = None,
	lead_time_map: dict | None = None,
	bundle_buildable_map: dict | None = None,
	reserve_pct: float = 0,
	max_publish_qty: int = 0,
) -> dict:
	stock_info = (
		stock_info_map.get(pricing.proxy_sku, {})
		if stock_info_map is not None
		else get_stock_info(pricing.proxy_sku)
	)

	active = pricing.active_strategy
	is_fba = active and active == pricing.fba_strategy
	is_sfp = active and active == pricing.sfp_strategy
	is_backorder = active and active == pricing.backorder_strategy

	if is_fba and stock_info.get("fba_stock", 0) > 0:
		return {
			"quantity": 0,
			"fulfillment_channel": "AMAZON_EU",
			"restock_date": None,
			"lead_time_days": 0,
			"shipping_group": None,
			"is_fba": True,
		}

	sku_info = (proxy_sku_info or {}).get(pricing.proxy_sku)
	if sku_info:
		item_code = sku_info["item"]
		pool_size = sku_info["pool_size"]
		pack_size = sku_info["pack_size"]
		sku_shipping_group_choice = sku_info.get("merchant_shipping_group")
	else:
		proxy_sku_row = (
			frappe.db.get_value(
				"Proxy SKU",
				pricing.proxy_sku,
				["item", "uom", "merchant_shipping_group"],
				as_dict=True,
			)
			or {}
		)
		item_code = proxy_sku_row.get("item")
		proxy_uom = proxy_sku_row.get("uom")
		sku_shipping_group_choice = proxy_sku_row.get("merchant_shipping_group")
		pool_size = frappe.db.count("Proxy SKU", filters={"item": item_code, "is_active": 1}) or 1
		pack_size = (
			flt(
				frappe.db.get_value(
					"UOM Conversion Detail",
					{"parent": item_code, "parenttype": "Item", "uom": proxy_uom},
					"conversion_factor",
				)
			)
			or 1.0
		)

	raw_qty = flt(stock_info.get("mfn_stock", 0))
	warehouse_qty = calculate_adjusted_qty(raw_qty, pool_size, pack_size)

	if supplier_data_map is not None and item_code:
		supplier_entry = supplier_data_map.get(item_code, {})
		top_supplier = supplier_entry.get("top_supplier")
		in_stock_supplier = supplier_entry.get("in_stock_supplier")
	else:
		top_supplier = get_top_supplier_for_item(item_code) if item_code else None
		in_stock_supplier = None

	resolved_supplier = top_supplier
	if is_backorder and item_code:
		if supplier_data_map is None:
			in_stock_supplier = get_best_in_stock_supplier_for_item(item_code)
		if in_stock_supplier:
			resolved_supplier = in_stock_supplier

	if lead_time_map is not None and resolved_supplier:
		lead_time_days = lead_time_map.get(resolved_supplier.supplier, 1)
	else:
		lead_time_days = get_supplier_lead_time(resolved_supplier.supplier) if resolved_supplier else 1

	resolved_supplier_stock = resolved_supplier.get("stock_qty", 0) if resolved_supplier else 0
	resolved_supplier_qty = calculate_adjusted_qty(resolved_supplier_stock, pool_size, pack_size)

	if not is_backorder and warehouse_qty > 0 and item_code:
		if bundle_buildable_map is None:
			bundle_buildable_map = get_buildable_bundle_qty_map([item_code])
		buildable_bundles = bundle_buildable_map.get(item_code)
		# Defensive cap; redundant while get_stock_info already sources bundle mfn_stock from buildable.
		if buildable_bundles is not None and buildable_bundles < warehouse_qty * pack_size:
			warehouse_qty = 0

	# Reserve/cap applies to own warehouse stock only, never to backorder/supplier quantity.
	quantity = (
		resolved_supplier_qty
		if is_backorder
		else apply_stock_guard(warehouse_qty, reserve_pct, max_publish_qty)
	)

	if zero_stock_proxy_skus is not None and pricing.proxy_sku in zero_stock_proxy_skus:
		quantity = 0

	restock_date = None
	if stock_info.get("has_restock_date", False):
		po_date = stock_info.get("restock_date")
		restock_date = cstr(po_date) if po_date else cstr(add_days(today(), lead_time_days))

	if is_sfp:
		is_two_day = (sku_shipping_group_choice or "").strip().lower() == "twoday"
		shipping_group = SHIPPING_GROUPS["two_day"] if is_two_day else SHIPPING_GROUPS["sfp"]
	else:
		shipping_group = SHIPPING_GROUPS["main_revised"]

	return {
		"quantity": quantity,
		"fulfillment_channel": "DEFAULT",
		"restock_date": restock_date,
		"lead_time_days": lead_time_days,
		"send_lead_time": not is_sfp,
		"shipping_group": shipping_group,
		"is_fba": False,
		"resolved_suppliers": {
			"top_supplier": top_supplier,
			"in_stock_supplier": resolved_supplier if is_backorder else None,
		},
	}


def get_allowed_pricing_proxy_skus() -> set | None:
	settings = frappe.get_cached_doc("Navgold Settings")
	mode = settings.pricing_publishing_mode or "Publish All Prices"

	if mode == "Publish All Prices":
		return None

	if mode == "Publish Selected Prices Only":
		return {row.proxy_sku for row in settings.published_pricing_items if row.proxy_sku}

	return None


def get_zero_stock_proxy_skus() -> set | None:
	settings = frappe.get_cached_doc("Navgold Settings")
	mode = settings.stock_publishing_mode or "Publish Actual Stock"

	if mode == "Publish Actual Stock":
		return None

	if mode == "Publish Zero Stock for All Items":
		all_proxy_skus = frappe.get_all("Proxy SKU", filters={"is_active": 1}, pluck="name")
		return set(all_proxy_skus)

	if mode == "Publish Zero Stock for Selected Items":
		return {row.proxy_sku for row in settings.zero_stock_items if row.proxy_sku}

	return None


def build_inventory_patches(inventory_data: dict, marketplace_id: str) -> list:
	patches = []

	if inventory_data["is_fba"]:
		patches.append(
			{
				"op": "replace",
				"path": "/attributes/fulfillment_availability",
				"value": [{"fulfillment_channel_code": "AMAZON_EU"}],
			}
		)
	else:
		fulfillment_value = {
			"quantity": inventory_data["quantity"],
			"fulfillment_channel_code": "DEFAULT",
		}
		if inventory_data.get("send_lead_time"):
			fulfillment_value["lead_time_to_ship_max_days"] = inventory_data["lead_time_days"]
		if inventory_data.get("restock_date"):
			fulfillment_value["restock_date"] = inventory_data["restock_date"]

		patches.append(
			{
				"op": "replace",
				"path": "/attributes/fulfillment_availability",
				"value": [fulfillment_value],
			}
		)

		if inventory_data.get("shipping_group"):
			patches.append(
				{
					"op": "replace",
					"path": "/attributes/merchant_shipping_group",
					"value": [{"value": inventory_data["shipping_group"], "marketplace_id": marketplace_id}],
				}
			)

	return patches


def build_pricing_feed(pricing_names: list, seller_id: str, marketplace_id: str) -> dict:
	messages = []
	fallback_repricings = []
	message_id = 1
	pricing_records = frappe.get_all(
		"Amazon Product Pricing",
		filters={"name": ["in", pricing_names], "is_active": True},
		fields=[
			"name",
			"proxy_sku",
			"current_min_price",
			"current_max_price",
			"is_active",
			"active_strategy",
			"fba_strategy",
			"sfp_strategy",
			"backorder_strategy",
		],
	)

	allowed_pricing_proxy_skus = get_allowed_pricing_proxy_skus()
	if allowed_pricing_proxy_skus is not None:
		pricing_records = [
			pricing for pricing in pricing_records if pricing.proxy_sku in allowed_pricing_proxy_skus
		]

	publishing_mode = (
		frappe.get_cached_value("Navgold Settings", "Navgold Settings", "amazon_publishing_mode")
		or "Stock Only"
	)
	send_prices = publishing_mode == "Stock and Prices"

	# Price guards only gate price-bearing feeds; a stock-only feed must keep updating
	# stock even for SKUs whose pricing data is currently invalid.
	blocked_price_deviation = []
	blocked_validation = []
	if send_prices:
		pricing_records, blocked_price_deviation = partition_pricing_by_price_deviation(pricing_records)

		violations_by_pricing = collect_pricing_violations(
			[pricing.name for pricing in pricing_records], include_deviation=False
		)
		if violations_by_pricing:
			blocked_validation = [
				{"proxy_sku": pricing.proxy_sku, "reasons": violations_by_pricing[pricing.name]}
				for pricing in pricing_records
				if pricing.name in violations_by_pricing
			]
			pricing_records = [
				pricing for pricing in pricing_records if pricing.name not in violations_by_pricing
			]

	proxy_sku_names = [pricing.proxy_sku for pricing in pricing_records if pricing.proxy_sku]
	proxy_sku_info = build_proxy_sku_info_map(proxy_sku_names) if proxy_sku_names else {}

	stock_info_map = get_stock_info_batch(proxy_sku_names) if proxy_sku_names else {}

	all_item_codes = list({info["item"] for info in proxy_sku_info.values() if info.get("item")})
	supplier_data_map = get_supplier_data_batch(all_item_codes) if all_item_codes else {}

	all_supplier_names = {
		entry[key].supplier
		for entry in supplier_data_map.values()
		for key in ("top_supplier", "in_stock_supplier")
		if entry.get(key)
	}
	lead_time_map = get_lead_time_batch(list(all_supplier_names)) if all_supplier_names else {}

	# used later by recompute_backorder_prices_with_fallback_supplier
	backorder_strategy_names = list(
		{
			pricing.backorder_strategy
			for pricing in pricing_records
			if pricing.backorder_strategy and pricing.active_strategy == pricing.backorder_strategy
		}
	)
	backorder_strategy_map = get_backorder_strategy_map(backorder_strategy_names)

	zero_stock_proxy_skus = get_zero_stock_proxy_skus()

	bundle_buildable_map = get_buildable_bundle_qty_map(all_item_codes) if all_item_codes else {}

	settings = frappe.get_cached_value(
		"Navgold Settings",
		"Navgold Settings",
		["send_min_price_to_amazon", "stock_reserve_pct", "max_publish_qty"],
		as_dict=True,
	)
	send_min_price = settings.send_min_price_to_amazon
	reserve_pct = flt(settings.stock_reserve_pct)
	max_publish_qty = cint(settings.max_publish_qty)

	for pricing in pricing_records:
		try:
			inventory_data = get_sku_inventory_data(
				pricing,
				zero_stock_proxy_skus,
				proxy_sku_info,
				stock_info_map,
				supplier_data_map,
				lead_time_map,
				bundle_buildable_map,
				reserve_pct,
				max_publish_qty,
			)
		except Exception as e:
			frappe.log_error(f"Skipping inventory patches for {pricing.name}: {e!s}", "Amazon Feed Builder")
			inventory_data = None

		patches = []

		if send_prices:
			final_min_price = flt(pricing.current_min_price)
			final_max_price = flt(pricing.current_max_price)

			try:
				resolved_suppliers = inventory_data.get("resolved_suppliers") if inventory_data else None
				fallback_result = recompute_backorder_prices_with_fallback_supplier(
					pricing,
					resolved_suppliers=resolved_suppliers,
					backorder_strategy_map=backorder_strategy_map,
				)
			except Exception as e:
				frappe.log_error(
					f"Backorder price recompute failed for {pricing.name}: {e!s}",
					"Amazon Feed Builder",
				)
				fallback_result = None

			if fallback_result:
				final_min_price = fallback_result["final_min_price"]
				final_max_price = fallback_result["final_max_price"]
				fallback_repricings.append(fallback_result["fallback_repricing"])

			# Always send the min floor as a safety net: it caps how low any downstream
			# repricer (Repricer.com) can drive the Amazon price. The send_min_price
			# toggle only controls whether we also assert our_price.
			purchasable_offer = {
				"audience": "ALL",
				"maximum_seller_allowed_price": [
					{"schedule": [{"value_with_tax": round(final_max_price, 2)}]}
				],
				"minimum_seller_allowed_price": [
					{"schedule": [{"value_with_tax": round(final_min_price, 2)}]}
				],
			}

			if send_min_price:
				purchasable_offer["our_price"] = [
					{"schedule": [{"value_with_tax": round(final_max_price, 2)}]}
				]

			patches.append(
				{
					"op": "replace",
					"path": "/attributes/purchasable_offer",
					"value": [purchasable_offer],
				}
			)

		if inventory_data:
			patches.extend(build_inventory_patches(inventory_data, marketplace_id))

		if not patches:
			frappe.log_error(f"Skipping {pricing.name}: no patches to send", "Amazon Feed Builder")
			continue

		message = {
			"messageId": message_id,
			"sku": pricing.proxy_sku,
			"operationType": "PATCH",
			"productType": "PRODUCT",
			"patches": patches,
		}

		messages.append(message)
		message_id += 1
	feed = {
		"header": {"sellerId": seller_id, "version": "2.0", "issueLocale": "en_GB"},
		"marketplaceIds": [marketplace_id],
		"messages": messages,
	}

	return {
		"feed_content": orjson_dumps(feed),
		"fallback_repricings": fallback_repricings,
		"blocked_price_deviation": blocked_price_deviation,
		"blocked_validation": blocked_validation,
		"publishing_mode": publishing_mode,
	}


def validate_pricing_feed(json_content: str, require_pricing_patches: bool = True) -> dict:
	errors = []

	try:
		feed = frappe.parse_json(json_content)
	except Exception as e:
		return {"valid": False, "message_count": 0, "errors": [f"JSON Parse Error: {e!s}"]}

	header = feed.get("header")
	if header is None:
		errors.append("Missing header object")
	else:
		if not header.get("sellerId"):
			errors.append("Missing sellerId in header")
		if not header.get("version"):
			errors.append("Missing version in header")

	if not feed.get("marketplaceIds"):
		errors.append("Missing marketplaceIds array")

	messages = feed.get("messages", [])
	message_count = len(messages)

	if message_count == 0:
		errors.append("No messages found in feed")

	for message_index, message in enumerate(messages, start=1):
		if not message.get("messageId"):
			errors.append(f"Message {message_index}: Missing messageId")

		if not message.get("sku"):
			errors.append(f"Message {message_index}: Missing sku")

		if message.get("operationType") != "PATCH":
			errors.append(f"Message {message_index}: operationType must be 'PATCH'")

		if not message.get("productType"):
			errors.append(f"Message {message_index}: Missing productType")

		patches = message.get("patches", [])
		if not patches:
			errors.append(f"Message {message_index}: No patches found")

		has_purchasable_offer = False
		has_fulfillment_availability = False
		for patch_index, patch in enumerate(patches, start=1):
			op = patch.get("op")
			if not op:
				errors.append(f"Message {message_index}, Patch {patch_index}: Missing op")
			elif op not in ("replace", "add", "delete"):
				errors.append(f"Message {message_index}, Patch {patch_index}: Invalid op '{op}'")

			if not patch.get("path"):
				errors.append(f"Message {message_index}, Patch {patch_index}: Missing path")
			if "value" not in patch:
				errors.append(f"Message {message_index}, Patch {patch_index}: Missing value")
			else:
				value = patch["value"]
				if not isinstance(value, list):
					errors.append(f"Message {message_index}, Patch {patch_index}: value must be an array")
				elif len(value) == 0:
					errors.append(f"Message {message_index}, Patch {patch_index}: value array is empty")

			path = patch.get("path", "")

			if path == "/attributes/purchasable_offer":
				has_purchasable_offer = True
				offer = patch["value"][0] if isinstance(patch.get("value"), list) and patch["value"] else {}
				if not offer.get("maximum_seller_allowed_price"):
					errors.append(
						f"Message {message_index}: Missing maximum_seller_allowed_price in purchasable_offer"
					)

			if path == "/attributes/fulfillment_availability" and op in ("add", "replace"):
				has_fulfillment_availability = True
				fulfillment_availability_value = (
					patch["value"][0] if isinstance(patch.get("value"), list) and patch["value"] else {}
				)
				if not fulfillment_availability_value.get("fulfillment_channel_code"):
					errors.append(
						f"Message {message_index}: Missing fulfillment_channel_code in fulfillment_availability"
					)
				if fulfillment_availability_value.get("fulfillment_channel_code") == "DEFAULT":
					qty = fulfillment_availability_value.get("quantity")
					if qty is not None and qty < 0:
						errors.append(f"Message {message_index}: quantity must be non-negative for MFN items")

		if require_pricing_patches and not has_purchasable_offer:
			errors.append(f"Message {message_index}: Missing purchasable_offer patch")

		if not has_fulfillment_availability:
			errors.append(f"Message {message_index}: Missing fulfillment_availability patch")

	return {"valid": len(errors) == 0, "message_count": message_count, "errors": errors}
