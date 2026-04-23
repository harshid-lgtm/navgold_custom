# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import math

import frappe
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count
from frappe.utils import add_days, getdate, now_datetime, today
from frappe.utils.caching import redis_cache
from frappe.utils.data import cint, cstr, flt, orjson_dumps

from navgold_custom.navgold_custom.amazon_pricing.strategy_selector import get_stock_info

SHIPPING_GROUPS = {
	"sfp": "57d54587-7341-4c9f-b3f9-546c8d798b21",
	"main_revised": "a31ca016-36ab-481f-b399-b61d727c93d3",
}


def get_top_supplier_for_item(item_code: str) -> dict | None:
	suppliers = frappe.get_all(
		"Supplier Item Price and Stock",
		filters={"item": item_code, "purchase_blocked": 0},
		fields=["supplier", "stock_qty", "rank"],
		order_by="rank asc",
		limit=1,
	)
	return suppliers[0] if suppliers else None


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


# This is a bit complicated
# You have a Proxy SKU which maps to an Item and UOM. The Item can have multiple active Proxy SKUs (pool), and the UOM has a conversion factor (pack size). So we divide the stock into all the active Proxy Skus
def build_proxy_sku_info_map(proxy_sku_names: list) -> dict:
	proxy_sku_rows = frappe.get_all(
		"Proxy SKU",
		filters={"name": ["in", proxy_sku_names]},
		fields=["name", "item", "uom"],
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
		result[proxy_sku] = {"item": row.item, "uom": row.uom, "pool_size": pool_size, "pack_size": pack_size}

	return result


def calculate_adjusted_qty(raw_qty: float, pool_size: int, pack_size: float) -> int:
	if pool_size <= 0 or pack_size <= 0:
		return 0
	return max(math.floor(raw_qty / pool_size / pack_size), 0)


def get_sku_inventory_data(
	pricing: dict,
	zero_stock_proxy_skus: set | None = None,
	proxy_sku_info: dict | None = None,
) -> dict:
	stock_info = get_stock_info(pricing.proxy_sku)

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
	else:
		item_code = frappe.db.get_value("Proxy SKU", pricing.proxy_sku, "item")
		proxy_uom = frappe.db.get_value("Proxy SKU", pricing.proxy_sku, "uom")
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

	top_supplier = get_top_supplier_for_item(item_code) if item_code else None
	lead_time_days = get_supplier_lead_time(top_supplier.supplier) if top_supplier else 1
	top_supplier_stock = top_supplier.get("stock_qty", 0) if top_supplier else 0
	top_supplier_qty = calculate_adjusted_qty(top_supplier_stock, pool_size, pack_size)

	quantity = top_supplier_qty if is_backorder else warehouse_qty

	if zero_stock_proxy_skus is not None and pricing.proxy_sku in zero_stock_proxy_skus:
		quantity = 0

	restock_date = None
	if stock_info.get("has_restock_date", False):
		po_date = stock_info.get("restock_date")
		restock_date = cstr(po_date) if po_date else cstr(add_days(today(), lead_time_days))

	shipping_group = SHIPPING_GROUPS["sfp"] if is_sfp else SHIPPING_GROUPS["main_revised"]

	return {
		"quantity": quantity,
		"fulfillment_channel": "DEFAULT",
		"restock_date": restock_date,
		"lead_time_days": lead_time_days,
		"send_lead_time": not is_sfp,
		"shipping_group": shipping_group,
		"is_fba": False,
	}


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


def build_pricing_feed(pricing_names: list, seller_id: str, marketplace_id: str) -> str:
	messages = []
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

	proxy_sku_names = [pricing.proxy_sku for pricing in pricing_records if pricing.proxy_sku]
	proxy_sku_info = build_proxy_sku_info_map(proxy_sku_names) if proxy_sku_names else {}

	zero_stock_proxy_skus = get_zero_stock_proxy_skus()

	for pricing in pricing_records:
		if not pricing.current_min_price or not pricing.current_max_price:
			frappe.log_error(f"Skipping {pricing.name}: No calculated prices", "Amazon Feed Builder")
			continue

		patches = [
			{
				"op": "replace",
				"path": "/attributes/purchasable_offer",
				"value": [
					{
						"audience": "ALL",
						"our_price": [
							{"schedule": [{"value_with_tax": round(pricing.current_max_price, 2)}]}
						],
						"minimum_seller_allowed_price": [
							{"schedule": [{"value_with_tax": round(pricing.current_min_price, 2)}]}
						],
						"maximum_seller_allowed_price": [
							{"schedule": [{"value_with_tax": round(pricing.current_max_price, 2)}]}
						],
					}
				],
			},
		]

		try:
			inventory_data = get_sku_inventory_data(pricing, zero_stock_proxy_skus, proxy_sku_info)
		except Exception as e:
			frappe.log_error(f"Skipping inventory patches for {pricing.name}: {e!s}", "Amazon Feed Builder")
			inventory_data = None

		if inventory_data:
			patches.extend(build_inventory_patches(inventory_data, marketplace_id))

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

	return orjson_dumps(feed)


def validate_pricing_feed(json_content: str) -> dict:
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
				if not offer.get("our_price"):
					errors.append(f"Message {message_index}: Missing our_price in purchasable_offer")

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

		if not has_purchasable_offer:
			errors.append(f"Message {message_index}: Missing purchasable_offer patch")

		if not has_fulfillment_availability:
			errors.append(f"Message {message_index}: Missing fulfillment_availability patch")

	return {"valid": len(errors) == 0, "message_count": message_count, "errors": errors}
