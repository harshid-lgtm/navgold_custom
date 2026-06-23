# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import base64
import csv
import io

import frappe
from frappe import _
from frappe.integrations.utils import make_get_request, make_post_request
from frappe.utils import flt, now_datetime

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
	format_supplier_fallback_notes,
	get_allowed_pricing_proxy_skus,
	recompute_backorder_prices_with_fallback_supplier,
)
from navgold_custom.navgold_custom.amazon_pricing.price_guard import (
	notify_price_deviation_blocks,
	partition_pricing_by_price_deviation,
)
from navgold_custom.navgold_custom.amazon_pricing.pricing_validators import (
	collect_pricing_violations,
	format_blocked_records,
)
from navgold_custom.navgold_custom.utils import (
	AmazonFrozenError,
	PricingNotApprovedError,
	block_if_amazon_frozen,
	block_if_pricing_not_approved,
	bulk_set_value,
)

REPRICER_API_BASE = "https://api.repricer.com/v1/repricers"


class RepricerClient:
	def __init__(self):
		settings = frappe.get_single("Navgold Settings")
		username = settings.repricer_api_username
		password = settings.get_password("repricer_api_password")

		if not username or not password:
			frappe.throw(_("Repricer.com API credentials not configured"))

		self.username = username
		self.password = password
		self._session = None
		self._token = None

	def get_auth_headers(self) -> dict:
		credentials = f"{self.username}:{self.password}"
		encoded = base64.b64encode(credentials.encode()).decode()
		return {"Authorization": f"Basic {encoded}", "Content-Type": "application/json"}

	def post_repricer_data(self, products) -> dict:
		block_if_amazon_frozen()
		block_if_pricing_not_approved()
		headers = self.get_auth_headers()
		return make_post_request(url=REPRICER_API_BASE, data=products, headers=headers)

	def get_repricer_data(self) -> dict:
		headers = self.get_auth_headers()
		return make_get_request(url=REPRICER_API_BASE, headers=headers)


def sync_to_repricer(pricing_names: list) -> dict:
	settings = frappe.get_single("Navgold Settings")
	amazon_settings = frappe.get_single("Amazon SP API Settings")

	if not settings.repricer_enabled:
		frappe.throw(_("Repricer.com integration is not enabled"))

	client = RepricerClient()

	log = frappe.new_doc("Amazon Pricing Submission Log")
	log.submission_type = "Repricer"
	log.status = "Pending"
	log.pricing_records = "\n".join(pricing_names)
	log.insert()

	pricing_records = {
		row.name: row
		for row in frappe.get_all(
			"Amazon Product Pricing",
			filters={"name": ["in", pricing_names]},
			fields=[
				"name",
				"proxy_sku",
				"is_active",
				"current_min_price",
				"current_max_price",
				"active_strategy",
				"backorder_strategy",
			],
		)
	}

	proxy_sku_names = [row.proxy_sku for row in pricing_records.values() if row.proxy_sku]
	fba_map = {
		row.name: row.is_fba
		for row in frappe.get_all(
			"Proxy SKU", filters={"name": ["in", proxy_sku_names]}, fields=["name", "is_fba"]
		)
	}

	strategy_names = [row.active_strategy for row in pricing_records.values() if row.active_strategy]
	strategy_map = {
		row.name: row.repricer_rule
		for row in frappe.get_all(
			"Amazon Product Strategy",
			filters={"name": ["in", strategy_names]},
			fields=["name", "repricer_rule"],
		)
	}

	# Apply the same price-deviation guard as the Amazon feed so crashed/spiked prices are not synced
	blocked_price_deviation = partition_pricing_by_price_deviation(list(pricing_records.values()))[1]
	blocked_proxy_skus = {record["proxy_sku"] for record in blocked_price_deviation}

	violations_by_pricing = collect_pricing_violations(pricing_names, include_deviation=False)

	blocked_reasons: dict[str, list[str]] = {}
	for pricing_name, reasons in violations_by_pricing.items():
		pricing = pricing_records.get(pricing_name)
		proxy_sku = pricing.proxy_sku if pricing else pricing_name
		blocked_reasons.setdefault(proxy_sku, []).extend(reasons)
	for record in blocked_price_deviation:
		blocked_reasons.setdefault(record["proxy_sku"], []).append(record["reason"])

	if blocked_reasons:
		log.blocked_records = format_blocked_records(blocked_reasons)
		log.blocked_count = len(blocked_reasons)

	# We could have used get_all with filters but we will loose the reason
	products_to_update = []
	errors = []
	fallback_repricings = []
	for pricing_name in pricing_names:
		try:
			pricing = pricing_records.get(pricing_name)
			if not pricing:
				errors.append(f"{pricing_name}: Not found")
				continue

			if not pricing.is_active:
				errors.append(f"{pricing_name}: Inactive")
				continue

			if pricing_name in violations_by_pricing:
				errors.append(f"{pricing_name}: {'; '.join(violations_by_pricing[pricing_name])}")
				continue

			if pricing.proxy_sku in blocked_proxy_skus:
				errors.append(f"{pricing_name}: Blocked by price-deviation guard")
				continue

			is_fba = fba_map.get(pricing.proxy_sku)
			repricer_rule = strategy_map.get(pricing.active_strategy) or ""

			final_min = flt(pricing.current_min_price)
			final_max = flt(pricing.current_max_price)
			fallback_result = recompute_backorder_prices_with_fallback_supplier(pricing)
			if fallback_result:
				final_min = fallback_result["final_min_price"]
				final_max = fallback_result["final_max_price"]
				fallback_repricings.append(fallback_result["fallback_repricing"])

			product_update = {
				"sku": pricing.proxy_sku,
				"marketplace": "AUK",
				"merchant_id": amazon_settings.merchant_id,
				"price_min": final_min,
				"price_max": final_max,
				"unit_cost": 0.0,
				"unit_currency": "GBP",
				"shipping_cost": 0.0,
				"shipping_currency": "GBP",
				"pickpack_cost": 0.0,
				"pickpack_currency": "GBP",
				"vat_rate": 0.0,
				"fba_fee": 0.0,
				"fba_currency": "GBP",
				"repricer_name": repricer_rule,
				"fee_listing": 15,
				"rrp_price": 0.0,
				"rrp_currency": "GBP",
				"fba": "YES" if is_fba else "NO",
			}

			products_to_update.append(product_update)

		except Exception as e:
			errors.append(f"{pricing_name}: {e!s}")

	if not products_to_update:
		log.status = "Error"
		log.error_message = "No valid products to sync\n" + "\n".join(errors)
		log.save()
		notify_price_deviation_blocks(blocked_price_deviation, "Repricer")
		frappe.throw(_("No valid products to sync"))

	try:
		csv_data = dict_list_to_csv(products_to_update)
		response = client.post_repricer_data(csv_data)
	except (AmazonFrozenError, PricingNotApprovedError) as e:
		# Intentional gate (kill switch or Dry Run), not a transport failure. Mark the log
		# "Skipped" so it stays out of the failed-sync triage queue and doesn't read as a real error.
		log.status = "Skipped"
		log.error_message = str(e)
		log.save()
		return {
			"synced": 0,
			"skipped": True,
			"error": str(e),
			"errors": errors,
			"log_name": log.name,
			"blocked_price_deviation": blocked_price_deviation,
			"blocked_validation": violations_by_pricing,
		}
	except Exception as e:
		log.status = "Error"
		log.error_message = str(e)
		log.save()
		frappe.log_error(f"Repricer sync error: {e!s}", "Repricer Sync")
		frappe.throw(_("Repricer sync failed: {0}").format(str(e)))

	log.status = "Completed"
	log.completed_at = now_datetime()
	log.feed_content = csv_data
	log.response_content = frappe.as_json(response)
	if fallback_repricings:
		log.supplier_swap_notes = format_supplier_fallback_notes(fallback_repricings)
	log.save()

	errored_names = {e.split(":")[0] for e in errors}
	synced_names = [name for name in pricing_names if name not in errored_names]
	if synced_names:
		bulk_set_value("Amazon Product Pricing", synced_names, "last_repricer_sync", now_datetime())

	return {
		"synced": len(products_to_update),
		"errors": errors,
		"log_name": log.name,
		"blocked_price_deviation": blocked_price_deviation,
		"blocked_validation": violations_by_pricing,
	}


def submit_active_repricer() -> dict:
	pricing_rows = frappe.get_all(
		"Amazon Product Pricing",
		filters={"is_active": 1},
		fields=["name", "proxy_sku"],
	)

	allowed_pricing_proxy_skus = get_allowed_pricing_proxy_skus()
	if allowed_pricing_proxy_skus is not None:
		pricing_rows = [row for row in pricing_rows if row.proxy_sku in allowed_pricing_proxy_skus]

	pricing_names = [row.name for row in pricing_rows]

	if not pricing_names:
		return {"success": True, "message": "No eligible pricing records to sync", "synced_count": 0}

	result = sync_to_repricer(pricing_names)

	notify_price_deviation_blocks(result.get("blocked_price_deviation") or [], "Repricer")

	result["synced_count"] = len(pricing_names)
	return result


@frappe.whitelist()
def bulk_sync_to_repricer(pricing_names: str) -> dict:
	if isinstance(pricing_names, str):
		try:
			pricing_names = frappe.parse_json(pricing_names)
		except Exception:
			pricing_names = [p.strip() for p in pricing_names.split(",") if p.strip()]

	if not pricing_names:
		frappe.throw(_("No pricing records provided"))

	return sync_to_repricer(pricing_names)


def dict_list_to_csv(data: list[dict]) -> str:
	if not data:
		return ""

	output = io.StringIO()
	writer = csv.DictWriter(output, fieldnames=data[0].keys(), extrasaction="ignore")

	writer.writeheader()
	writer.writerows(data)

	return output.getvalue()
