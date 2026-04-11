# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import base64
import csv
import io

import frappe
from frappe import _
from frappe.integrations.utils import make_get_request, make_post_request
from frappe.utils import flt, now_datetime

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

	# Create submission log
	log = frappe.new_doc("Amazon Pricing Submission Log")
	log.submission_type = "Repricer"
	log.status = "Pending"
	log.pricing_records = "\n".join(pricing_names)
	log.insert()

	products_to_update = []
	errors = []
	for pricing_name in pricing_names:
		try:
			pricing = frappe.get_cached_doc("Amazon Product Pricing", pricing_name)
			is_fba = frappe.db.get_value("Proxy SKU", pricing.proxy_sku, "is_fba")

			if not pricing.is_active:
				errors.append(f"{pricing_name}: Inactive")
				continue

			if not pricing.current_min_price or not pricing.current_max_price:
				errors.append(f"{pricing_name}: No calculated prices")
				continue

			# Get repricer rule ID if available
			if not pricing.active_strategy:
				errors.append(f"{pricing_name}: No active strategy")
				continue

			strategy = frappe.get_cached_doc("Amazon Product Strategy", pricing.active_strategy)

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
				"repricer_name": strategy.repricer_rule or "",
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
		return {"success": False, "error": "No valid products to sync", "errors": errors}

	try:
		# Bulk update to Repricer.com
		csv_data = dict_list_to_csv(products_to_update)
		result = client.post_repricer_data(csv_data)
		print("this is result")
		print(result)
		log.status = "Completed"
		log.completed_at = now_datetime()
		log.feed_content = csv_data
		log.save()

		# Update pricing records
		for pricing_name in pricing_names:
			if pricing_name not in [e.split(":")[0] for e in errors]:
				frappe.db.set_value(
					"Amazon Product Pricing", pricing_name, "last_repricer_sync", now_datetime()
				)

		return {"success": True, "synced": len(products_to_update), "errors": errors, "log_name": log.name}

	except Exception as e:
		log.status = "Error"
		log.error_message = str(e)
		log.save()

		frappe.log_error(f"Repricer sync error: {e!s}", "Repricer Sync")

		return {"success": False, "error": str(e), "errors": errors, "log_name": log.name}


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
