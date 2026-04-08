# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import math

import frappe
from frappe.utils import add_days, getdate, today
from frappe.utils.caching import redis_cache
from frappe.utils.data import cint, flt, orjson_dumps

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


@redis_cache(ttl=3600 * 12)
def get_supplier_lead_time(supplier_name: str) -> int:
	today_day = getdate(today()).strftime("%A")
	lead_time = frappe.db.get_value(
		"Supplier Lead Time",
		{"parent": supplier_name, "parenttype": "Supplier", "day": today_day},
		"lead_time",
	)
	return cint(lead_time) or 1


def get_pool_and_pack_size(proxy_sku: str, item_code: str) -> tuple[int, float]:
	"""Get pool size (active proxy SKUs sharing this item) and pack size (UOM conversion factor)."""
	pool_size = frappe.db.count("Proxy SKU", filters={"item": item_code, "is_active": 1}) or 1

	proxy_uom = frappe.db.get_value("Proxy SKU", proxy_sku, "uom")
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

	return pool_size, pack_size


def calculate_adjusted_qty(raw_qty: float, pool_size: int, pack_size: float) -> int:
	"""Adjust stock: floor(raw_qty / pool_size / pack_size)."""
	if pool_size <= 0 or pack_size <= 0:
		return 0
	return max(math.floor(raw_qty / pool_size / pack_size), 0)


def get_sku_inventory_data(pricing: dict, zero_stock_proxy_skus: set | None = None) -> dict:
	stock_info = get_stock_info(pricing.proxy_sku)

	pricing_doc = frappe.get_cached_doc("Amazon Product Pricing", pricing.name)
	active = pricing_doc.active_strategy
	is_fba = active and active == pricing_doc.fba_strategy
	is_sfp = active and active == pricing_doc.sfp_strategy

	if is_fba and stock_info.get("fba_stock", 0) > 0:
		return {
			"quantity": 0,
			"fulfillment_channel": "AMAZON_EU",
			"restock_date": None,
			"lead_time_days": 0,
			"shipping_group": None,
			"is_fba": True,
		}

	item_code = frappe.db.get_value("Proxy SKU", pricing.proxy_sku, "item")

	raw_qty = flt(stock_info.get("mfn_stock", 0))
	pool_size, pack_size = get_pool_and_pack_size(pricing.proxy_sku, item_code)
	quantity = calculate_adjusted_qty(raw_qty, pool_size, pack_size)

	if zero_stock_proxy_skus is not None and pricing.proxy_sku in zero_stock_proxy_skus:
		quantity = 0

	top_supplier = get_top_supplier_for_item(item_code) if item_code else None
	lead_time_days = get_supplier_lead_time(top_supplier.supplier) if top_supplier else 1

	restock_date = None
	if stock_info.get("has_restock_date", False):
		po_date = stock_info.get("restock_date")
		restock_date = str(po_date) if po_date else str(add_days(today(), lead_time_days))

	shipping_group = SHIPPING_GROUPS["sfp"] if is_sfp else SHIPPING_GROUPS["main_revised"]

	return {
		"quantity": quantity,
		"fulfillment_channel": "DEFAULT",
		"restock_date": restock_date,
		"lead_time_days": lead_time_days,
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


def _build_inventory_patches(inv: dict, marketplace_id: str) -> list:
	patches = []

	if inv["is_fba"]:
		# FBA: switch to AMAZON_EU channel
		patches.append(
			{
				"op": "add",
				"path": "/attributes/fulfillment_availability",
				"value": [{"fulfillment_channel_code": "AMAZON_EU"}],
			}
		)
		patches.append(
			{
				"op": "delete",
				"path": "/attributes/fulfillment_availability",
				"value": [{"fulfillment_channel_code": "DEFAULT"}],
			}
		)
	else:
		# MFN: set stock quantity, optional restock date and lead time
		fulfillment_value = {
			"quantity": inv["quantity"],
			"fulfillment_channel_code": "DEFAULT",
			"lead_time_to_ship_max_days": inv["lead_time_days"],
		}
		if inv.get("restock_date"):
			fulfillment_value["restock_date"] = inv["restock_date"]

		patches.append(
			{
				"op": "add",
				"path": "/attributes/fulfillment_availability",
				"value": [fulfillment_value],
			}
		)

		# Merchant shipping group
		if inv.get("shipping_group"):
			patches.append(
				{
					"op": "replace",
					"path": "/attributes/merchant_shipping_group",
					"value": [{"value": inv["shipping_group"], "marketplace_id": marketplace_id}],
				}
			)

		# Delete FBA channel for MFN items
		patches.append(
			{
				"op": "delete",
				"path": "/attributes/fulfillment_availability",
				"value": [{"fulfillment_channel_code": "AMAZON_EU"}],
			}
		)

	return patches


def build_pricing_feed(pricing_names: list, seller_id: str, marketplace_id: str) -> str:
	"""
	Build a JSON pricing feed for submission to Amazon.

	Uses the JSON_LISTINGS_FEED format with PATCH operations
	for updating pricing attributes.

	"""
	messages = []
	message_id = 1
	pricing_records = frappe.get_all(
		"Amazon Product Pricing",
		filters={"name": ["in", pricing_names], "is_active": True},
		fields=["name", "proxy_sku", "current_min_price", "current_max_price", "is_active"],
	)

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

		# Get inventory data and build fulfillment patches
		try:
			inv = get_sku_inventory_data(pricing, zero_stock_proxy_skus)
		except Exception as e:
			frappe.log_error(f"Skipping inventory patches for {pricing.name}: {e!s}", "Amazon Feed Builder")
			inv = None

		if inv:
			patches.extend(_build_inventory_patches(inv, marketplace_id))

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

	for i, message in enumerate(messages, start=1):
		if not message.get("messageId"):
			errors.append(f"Message {i}: Missing messageId")

		if not message.get("sku"):
			errors.append(f"Message {i}: Missing sku")

		if message.get("operationType") != "PATCH":
			errors.append(f"Message {i}: operationType must be 'PATCH'")

		if not message.get("productType"):
			errors.append(f"Message {i}: Missing productType")

		patches = message.get("patches", [])
		if not patches:
			errors.append(f"Message {i}: No patches found")

		has_purchasable_offer = False
		has_fulfillment_availability = False
		for j, patch in enumerate(patches, start=1):
			op = patch.get("op")
			if not op:
				errors.append(f"Message {i}, Patch {j}: Missing op")
			elif op not in ("replace", "add", "delete"):
				errors.append(f"Message {i}, Patch {j}: Invalid op '{op}'")

			if not patch.get("path"):
				errors.append(f"Message {i}, Patch {j}: Missing path")
			if "value" not in patch:
				errors.append(f"Message {i}, Patch {j}: Missing value")
			else:
				value = patch["value"]
				if not isinstance(value, list):
					errors.append(f"Message {i}, Patch {j}: value must be an array")
				elif len(value) == 0:
					errors.append(f"Message {i}, Patch {j}: value array is empty")

			path = patch.get("path", "")

			if path == "/attributes/purchasable_offer":
				has_purchasable_offer = True
				offer = patch["value"][0] if isinstance(patch.get("value"), list) and patch["value"] else {}
				if not offer.get("our_price"):
					errors.append(f"Message {i}: Missing our_price in purchasable_offer")

			if path == "/attributes/fulfillment_availability" and op in ("add", "replace"):
				has_fulfillment_availability = True
				fa_value = (
					patch["value"][0] if isinstance(patch.get("value"), list) and patch["value"] else {}
				)
				if not fa_value.get("fulfillment_channel_code"):
					errors.append(
						f"Message {i}: Missing fulfillment_channel_code in fulfillment_availability"
					)
				if fa_value.get("fulfillment_channel_code") == "DEFAULT":
					qty = fa_value.get("quantity")
					if qty is not None and qty < 0:
						errors.append(f"Message {i}: quantity must be non-negative for MFN items")

		if not has_purchasable_offer:
			errors.append(f"Message {i}: Missing purchasable_offer patch")

		if not has_fulfillment_availability:
			errors.append(f"Message {i}: Missing fulfillment_availability patch")

	return {"valid": len(errors) == 0, "message_count": message_count, "errors": errors}
