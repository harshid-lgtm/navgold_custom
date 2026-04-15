# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import base64
import csv
import io

import frappe
from frappe import _
from frappe.integrations.utils import make_get_request, make_post_request
from frappe.utils import flt, now_datetime

from navgold_custom.navgold_custom.utils import bulk_set_value

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
	# We could have used get_all with filters but we will loose the reason
	products_to_update = []
	errors = []
	for pricing_name in pricing_names:
		try:
			pricing = pricing_records.get(pricing_name)
			if not pricing:
				errors.append(f"{pricing_name}: Not found")
				continue

			if not pricing.is_active:
				errors.append(f"{pricing_name}: Inactive")
				continue

			if not pricing.current_min_price or not pricing.current_max_price:
				errors.append(f"{pricing_name}: No calculated prices")
				continue

			if not pricing.active_strategy:
				errors.append(f"{pricing_name}: No active strategy")
				continue

			is_fba = fba_map.get(pricing.proxy_sku)
			repricer_rule = strategy_map.get(pricing.active_strategy) or ""

			product_update = {
				"sku": pricing.proxy_sku,
				"marketplace": "AUK",
				"merchant_id": amazon_settings.merchant_id,
				"price_min": flt(pricing.current_min_price),
				"price_max": flt(pricing.current_max_price),
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
		frappe.throw(_("No valid products to sync"))

	try:
		csv_data = dict_list_to_csv(products_to_update)
		response = client.post_repricer_data(csv_data)
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
	log.save()

	errored_names = {e.split(":")[0] for e in errors}
	synced_names = [name for name in pricing_names if name not in errored_names]
	if synced_names:
		bulk_set_value("Amazon Product Pricing", synced_names, "last_repricer_sync", now_datetime())

	return {"synced": len(products_to_update), "errors": errors, "log_name": log.name}


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
