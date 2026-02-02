# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import frappe
from frappe.utils.data import orjson_dumps


def build_pricing_feed(pricing_names: list, seller_id: str, marketplace_id: str) -> str:
	"""
	Build a JSON pricing feed for submission to Amazon.

	Uses the JSON_LISTINGS_FEED format with PATCH operations
	for updating pricing attributes.

	"""
	messages = []
	message_id = 1

	for pricing_name in pricing_names:
		pricing = frappe.get_cached_doc("Amazon Product Pricing", pricing_name)

		if not pricing.is_active:
			continue

		if not pricing.current_min_price or not pricing.current_max_price:
			frappe.log_error(f"Skipping {pricing_name}: No calculated prices", "Amazon Feed Builder")
			continue

		patches = [
			{
				"op": "replace",
				"path": "/standard_price",
				"value": {"amount": round(pricing.current_min_price, 2), "currency": "GBP"},
			},
			{
				"op": "replace",
				"path": "/minimum_seller_allowed_price",
				"value": {"amount": round(pricing.current_min_price, 2), "currency": "GBP"},
			},
			{
				"op": "replace",
				"path": "/maximum_seller_allowed_price",
				"value": {"amount": round(pricing.current_max_price, 2), "currency": "GBP"},
			},
		]

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

		has_standard_price = False
		for j, patch in enumerate(patches, start=1):
			if not patch.get("op"):
				errors.append(f"Message {i}, Patch {j}: Missing op")
			if not patch.get("path"):
				errors.append(f"Message {i}, Patch {j}: Missing path")
			if "value" not in patch:
				errors.append(f"Message {i}, Patch {j}: Missing value")
			else:
				value = patch["value"]
				if not isinstance(value, dict):
					errors.append(f"Message {i}, Patch {j}: value must be an object")
				elif "amount" not in value:
					errors.append(f"Message {i}, Patch {j}: Missing amount in value")
				elif "currency" not in value:
					errors.append(f"Message {i}, Patch {j}: Missing currency in value")

			if patch.get("path") == "/standard_price":
				has_standard_price = True

		if not has_standard_price:
			errors.append(f"Message {i}: Missing standard_price patch")

	return {"valid": len(errors) == 0, "message_count": message_count, "errors": errors}
