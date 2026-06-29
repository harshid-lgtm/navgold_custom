# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

"""
Pull the live min/max seller-allowed price and selling price each SKU has on Amazon.
"""

from frappe.utils.data import flt

from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import Listings, Reports
from navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku import parse_listings_tsv

LISTINGS_REPORT_TYPE = "GET_MERCHANT_LISTINGS_ALL_DATA"


def extract_offer_value(offer: dict, field: str) -> float | None:
	# Amazon nests these as: field -> [{"schedule": [{"value_with_tax": X}]}].
	entries = offer.get(field) or []
	if not entries:
		return None
	schedule = entries[0].get("schedule") or []
	if not schedule:
		return None
	return flt(schedule[0].get("value_with_tax"))


def parse_purchasable_offer(attributes: dict) -> dict | None:
	offers = (attributes or {}).get("purchasable_offer") or []
	if not offers:
		return None
	offer = offers[0]
	return {
		"min": extract_offer_value(offer, "minimum_seller_allowed_price"),
		"max": extract_offer_value(offer, "maximum_seller_allowed_price"),
		"selling_price": extract_offer_value(offer, "our_price"),
	}


def get_active_seller_skus() -> list[str]:
	reports_client = Reports.from_amazon_settings()
	tsv_content = reports_client.request_and_download_report(LISTINGS_REPORT_TYPE)
	rows = parse_listings_tsv(tsv_content)
	return [
		row["seller-sku"]
		for row in rows
		if (row.get("status") or "").lower() == "active" and row.get("seller-sku")
	]


def fetch_live_prices() -> dict:
	skus = get_active_seller_skus()
	if not skus:
		return {}

	listings_client = Listings.from_amazon_settings()
	items = listings_client.search_items_by_skus(skus, included_data="attributes")

	prices = {}
	for item in items:
		sku = item.get("sku")
		if not sku:
			continue
		offer = parse_purchasable_offer(item.get("attributes") or {})
		if offer:
			prices[sku] = offer
	return prices
