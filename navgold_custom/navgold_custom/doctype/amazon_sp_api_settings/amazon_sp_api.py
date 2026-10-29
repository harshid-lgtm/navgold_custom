# Attribution: Code adapted from eFeone's eSeller Suite (https://github.com/efeone/eseller_suite)
# Original work licensed under the MIT License
# Copyright (c) 2024, eFeone and contributors
#
# Modifications and additions
# Copyright (c) 2025, BWH Studios and contributors
# For license information, please see license.txt
import gzip
import time

import frappe
from frappe.integrations.utils import make_get_request
from frappe.integrations.utils import make_request as frappe_make_request
from frappe.utils import cstr, flt, get_request_session
from frappe.utils.caching import redis_cache
from requests.exceptions import HTTPError

from navgold_custom.navgold_custom.utils import (
	block_if_amazon_frozen,
	block_if_pricing_not_approved,
)

MAX_RETRIES = 3
# Amazon Feeds (createFeed/createFeedDocument) restore throttle tokens at ~1 per 2 min, so a 429
# needs several backoff steps to ride out the window — hence more retries than the default.
FEED_MAX_RETRIES = 7
# Cap per-attempt sleep so exponential backoff (1,2,4,...) can't balloon into a multi-minute
# blocking wait.
MAX_BACKOFF_SECONDS = 30
LWA_AUTH_URL = "https://api.amazon.com/auth/o2/token"


# Frappe's make_get_request / make_put_request parse JSON and cannot handle raw binary payloads
# or S3 pre-signed URLs that expect exact Content-Type headers. These thin wrappers use the
# underlying requests session directly for those cases only.
def download_binary(url: str, timeout: int = 60) -> bytes:
	session = get_request_session()
	response = session.get(url, timeout=timeout)
	response.raise_for_status()
	return response.content


def upload_binary(url: str, content: bytes, content_type: str) -> None:
	session = get_request_session()
	response = session.put(url=url, data=content, headers={"Content-Type": content_type})
	response.raise_for_status()


@redis_cache(ttl=3500)  # Amazon LWA tokens expire in 3600s; 3500s gives buffer for clock skew
def fetch_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
	data = {
		"grant_type": "refresh_token",
		"client_id": client_id,
		"client_secret": client_secret,
		"refresh_token": refresh_token,
	}
	try:
		result = frappe_make_request("POST", LWA_AUTH_URL, data=data)
		if result and result.get("access_token"):
			return result.get("access_token")
		raise SPAPIError(
			error=result.get("error", "UnknownError"),
			error_description=result.get("error_description", "Failed to get access token"),
		)
	except Exception as e:
		if isinstance(e, SPAPIError):
			raise
		raise SPAPIError(
			error="AuthenticationError", error_description=f"Failed to authenticate with Amazon: {e!s}"
		) from None


def is_throttling_error(error) -> bool:
	return getattr(error, "status_code", None) in (429, 503)


def retry_with_backoff(func, error_title="SP-API Error", max_retries=MAX_RETRIES, throttle_only=False):
	retry_count = 0
	backoff = 1

	while retry_count <= max_retries:
		try:
			return func()
		except Exception as error:
			if throttle_only and not is_throttling_error(error):
				raise
			retry_count += 1
			if retry_count > max_retries:
				frappe.log_error(
					f"{error_title}: {error!s}",
					error_title,
				)
				raise
			time.sleep(min(backoff, MAX_BACKOFF_SECONDS))
			backoff *= 2


# https://github.com/amzn/selling-partner-api-docs/blob/main/guides/en-US/developer-guide/SellingPartnerApiDeveloperGuide.md
MARKETPLACES = {
	"North America": {
		"CA": "A2EUQ1WTGCTBG2",
		"US": "ATVPDKIKX0DER",
		"MX": "A1AM78C64UM0Y8",
		"BR": "A2Q3Y263D00KWC",
		"AWS Region": "us-east-1",
		"Endpoint": "https://sellingpartnerapi-na.amazon.com",
	},
	"Europe": {
		"ES": "A1RKKUPIHCS9HS",
		"GB": "A1F83G8C2ARO7P",
		"FR": "A13V1IB3VIYZZH",
		"NL": "A1805IZSGTT6HS",
		"DE": "A1PA6795UKMFR9",
		"IT": "APJ6JRA9NG5V4",
		"SE": "A2NODRKZP88ZB9",
		"PL": "A1C3SOZRARQ6R3",
		"EG": "ARBP9OOSHTCHU",
		"TR": "A33AVAJ2PDY3EV",
		"SA": "A17E79C6D8DWNP",
		"AE": "A2VIGQ35RCS4UG",
		"IN": "A21TJRUUN4KGV",
		"AWS Region": "eu-west-1",
		"Endpoint": "https://sellingpartnerapi-eu.amazon.com",
	},
	"Far East": {
		"SG": "A19VAU5U5O7RUS",
		"AU": "A39IBJ37TRP1C6",
		"JP": "A1VC38T7YXB528",
		"AWS Region": "us-west-2",
		"Endpoint": "https://sellingpartnerapi-fe.amazon.com",
	},
}

# Following code is adapted from https://github.com/andrewjroth/requests-auth-aws-sigv4 under the Apache License 2.0 with minor changes.

# Copyright 2020 Andrew J Roth <andrew@andrewjroth.com>

# Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

# 1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.

# 2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

# 3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.


class SPAPIError(Exception):
	"""
	Main SP-API Exception class
	"""

	def __init__(self, *args, **kwargs) -> None:
		self.error = kwargs.get("error", "-")
		self.error_description = kwargs.get("error_description", "-")
		self.status_code = kwargs.get("status_code")
		super().__init__(*args)


class SPAPI:
	# https://github.com/amzn/selling-partner-api-docs/blob/main/guides/en-US/developer-guide/SellingPartnerApiDeveloperGuide.md#connecting-to-the-selling-partner-api

	BASE_URI = "/"

	def __init__(
		self,
		client_id: str,
		client_secret: str,
		refresh_token: str,
		country_code: str = "IN",
	) -> None:
		self.client_id = client_id
		self.client_secret = client_secret
		self.refresh_token = refresh_token
		self.country_code = country_code
		self.region, self.endpoint, self.marketplace_id = Util.get_marketplace_data(country_code)

	@classmethod
	def from_amazon_settings(cls, settings=None):
		if not settings:
			settings = frappe.get_single("Amazon SP API Settings")
		instance = cls(
			client_id=settings.client_id,
			client_secret=settings.get_password("client_secret"),
			refresh_token=settings.get_password("refresh_token"),
			country_code=settings.country,
		)
		instance.settings = settings
		return instance

	def get_access_token(self) -> str:
		return fetch_access_token(self.client_id, self.client_secret, self.refresh_token)

	def get_headers(self) -> dict:
		return {"x-amz-access-token": self.get_access_token()}

	def make_request(
		self,
		method: str = "GET",
		append_to_base_uri: str = "",
		params: dict | None = None,
		data: dict | None = None,
		json: dict | None = None,
	) -> dict:
		if isinstance(params, dict):
			params = Util.remove_empty(params)
		if isinstance(data, dict):
			data = Util.remove_empty(data)

		url = self.endpoint + self.BASE_URI + append_to_base_uri
		headers = self.get_headers()
		if json is not None:
			headers["Content-Type"] = "application/json"

		try:
			return frappe_make_request(
				method=method, url=url, params=params, data=data, json=json, headers=headers
			)
		except SPAPIError:
			raise
		except HTTPError as e:
			if e.response is None:
				# `from None` drops the chained frames whose locals contain auth headers
				raise SPAPIError(error="HTTPError", error_description=cstr(e)) from None

			response_text = e.response.text or ""
			try:
				body = e.response.json()
				errors = body.get("errors", [])
				# details carries the actionable reason; message is often generic
				description = (
					"; ".join(
						": ".join(
							part for part in (err.get("code"), err.get("message"), err.get("details")) if part
						)
						for err in errors
					)
					if errors
					else cstr(body)
				)
			except Exception:
				description = response_text or cstr(e)

			frappe.log_error(
				message=f"HTTP {e.response.status_code} response: {response_text}",
				title="SP-API HTTP Error Body",
			)
			raise SPAPIError(
				error=f"HTTP {e.response.status_code}",
				error_description=description,
				status_code=e.response.status_code,
			) from None
		except Exception as e:
			raise SPAPIError(
				error=type(e).__name__,
				error_description=cstr(e),
			) from None

	def list_to_dict(self, key: str, values: list, data: dict) -> None:
		if values and isinstance(values, list):
			for idx in range(len(values)):
				data[f"{key}[{idx}]"] = values[idx]


class Finances(SPAPI):
	BASE_URI = "/finances/v0/"

	def list_financial_events_by_order_id(
		self, order_id: str, max_results: int | None = None, next_token: str | None = None
	) -> dict:
		append_to_base_uri = f"orders/{order_id}/financialEvents"
		data = dict(MaxResultsPerPage=max_results, NextToken=next_token)
		return self.make_request(append_to_base_uri=append_to_base_uri, params=data)


class Orders(SPAPI):
	BASE_URI = "/orders/v0/orders"

	def get_orders(
		self,
		created_after: str | None = None,
		created_before: str | None = None,
		last_updated_after: str | None = None,
		last_updated_before: str | None = None,
		order_statuses: list | None = None,
		marketplace_ids: list | None = None,
		fulfillment_channels: list | None = None,
		payment_methods: list | None = None,
		buyer_email: str | None = None,
		seller_order_id: str | None = None,
		max_results: int = 100,
		easyship_shipment_statuses: list | None = None,
		next_token: str | None = None,
		amazon_order_ids: str | None = None,
		actual_fulfillment_supply_source_id: str | None = None,
		is_ispu: bool = False,
		store_chain_store_id: str | None = None,
	) -> dict:
		"""Returns orders created or updated during the time frame indicated by the specified parameters. You can also apply a range of filtering criteria to narrow the list of orders returned. If NextToken is present, that will be used to retrieve the orders instead of other criteria."""
		data = dict(
			CreatedAfter=created_after,
			CreatedBefore=created_before,
			LastUpdatedAfter=last_updated_after,
			LastUpdatedBefore=last_updated_before,
			BuyerEmail=buyer_email,
			SellerOrderId=seller_order_id,
			MaxResultsPerPage=max_results,
			NextToken=next_token,
			ActualFulfillmentSupplySourceId=actual_fulfillment_supply_source_id,
			IsISPU=is_ispu,
			StoreChainStoreId=store_chain_store_id,
		)

		self.list_to_dict("OrderStatuses", order_statuses, data)
		self.list_to_dict("MarketplaceIds", marketplace_ids, data)
		self.list_to_dict("FulfillmentChannels", fulfillment_channels, data)
		self.list_to_dict("PaymentMethods", payment_methods, data)
		self.list_to_dict("EasyShipShipmentStatuses", easyship_shipment_statuses, data)
		# self.list_to_dict("AmazonOrderIds", amazon_order_ids, data)

		if not marketplace_ids:
			marketplace_ids = [self.marketplace_id]
			data["MarketplaceIds"] = marketplace_ids

		if amazon_order_ids:
			data["AmazonOrderIds"] = amazon_order_ids

		return self.make_request(params=data)

	def get_order_items(self, order_id: str, next_token: str | None = None) -> dict:
		"""Returns detailed order item information for the order indicated by the specified order ID. If NextToken is provided, it's used to retrieve the next page of order items."""
		append_to_base_uri = f"/{order_id}/orderItems"
		data = dict(NextToken=next_token)
		return self.make_request(append_to_base_uri=append_to_base_uri, params=data)

	def get_order(self, order_id: str) -> dict:
		"""Method to get a Particular Order"""
		append_to_base_uri = f"/{order_id}"
		return self.make_request(append_to_base_uri=append_to_base_uri)

	def get_order_buyer_info(self, order_id: str) -> dict:
		"""Returns the buyer information for the order indicated by the specified order ID."""
		append_to_base_uri = f"/{order_id}/buyerInfo"
		return self.make_request(append_to_base_uri=append_to_base_uri)

	def get_order_address(self, order_id: str) -> dict:
		"""Returns the shipping address for the order indicated by the specified order ID."""
		append_to_base_uri = f"/{order_id}/address"
		return self.make_request(append_to_base_uri=append_to_base_uri)


class Shipping(SPAPI):
	"""Amazon Shipping API (Buy Shipping v2)"""

	BASE_URI = "/shipping/v2/"

	def get_headers(self):
		headers = super().get_headers()
		country_code = (self.country_code or "").upper()
		business_id_map = {
			"US": "AmazonShipping_US",
			"GB": "AmazonShipping_UK",
			"UK": "AmazonShipping_UK",
			"IN": "AmazonShipping_IN",
			"IT": "AmazonShipping_IT",
			"ES": "AmazonShipping_ES",
			"FR": "AmazonShipping_FR",
		}
		headers["x-amzn-shipping-business-id"] = business_id_map.get(country_code, "AmazonShipping_UK")
		return headers

	def get_shipping_rates(self, payload: dict) -> dict:
		return self.make_request(method="POST", append_to_base_uri="shipments/rates", json=payload)

	def purchase_shipment(
		self,
		rate_id: str,
		request_token: str = "",
		document_spec: dict | None = None,
		value_added_services: list | None = None,
	) -> dict:
		body = {"rateId": rate_id}
		if request_token:
			body["requestToken"] = request_token
		if document_spec:
			body["requestedDocumentSpecification"] = document_spec
		if value_added_services:
			body["requestedValueAddedServices"] = value_added_services
		return self.make_request(method="POST", append_to_base_uri="shipments", json=body)

	def get_shipment_documents(self, shipment_id: str) -> dict:
		return self.make_request(append_to_base_uri=f"shipments/{shipment_id}/documents")

	def cancel_shipment(self, shipment_id: str) -> dict:
		return self.make_request(method="PUT", append_to_base_uri=f"shipments/{shipment_id}/cancel")


class CatalogItems(SPAPI):
	"""Amazon Catalog Items API"""

	# BASE_URI = "/catalog/v0"
	BASE_URI = "/catalog/2022-04-01"

	def get_catalog_item(
		self,
		asin: str,
		marketplace_id: str | None = None,
		included_data: str | None = None,
	) -> dict:
		"""Return a catalog item, optionally including specific Amazon datasets."""
		if not marketplace_id:
			marketplace_id = self.marketplace_id

		append_to_base_uri = f"/items/{asin}"
		params = {"marketplaceIds": marketplace_id}
		if included_data:
			params["includedData"] = included_data

		return self.make_request(append_to_base_uri=append_to_base_uri, params=params)


class Util:
	@staticmethod
	def get_marketplace(country_code):
		for selling_region in MARKETPLACES:
			for country in MARKETPLACES.get(selling_region):
				if country_code == country:
					return MARKETPLACES.get(selling_region)
		else:
			raise KeyError(f"Invalid Country Code: {country_code}")

	@staticmethod
	def get_marketplace_data(country_code):
		marketplace = Util.get_marketplace(country_code)
		region = marketplace.get("AWS Region")
		endpoint = marketplace.get("Endpoint")
		marketplace_id = marketplace.get(country_code)

		return region, endpoint, marketplace_id

	@staticmethod
	def remove_empty(dict):
		for key in list(dict):
			if not dict[key]:
				del dict[key]
		return dict


class Listings(SPAPI):
	BASE_URI = "/listings/2021-08-01/"

	def __init__(self, *args, seller_id: str = "", **kwargs):
		super().__init__(*args, **kwargs)
		self.seller_id = seller_id

	@classmethod
	def from_amazon_settings(cls, settings=None):
		instance = super().from_amazon_settings(settings)
		instance.seller_id = instance.settings.merchant_id
		return instance

	def get_listing_item(
		self,
		seller_id: str = "",
		sku: str = "",
		marketplace_id: str | None = None,
		included_data: str = "summaries",
	) -> dict:
		if not seller_id:
			seller_id = self.seller_id
		if not marketplace_id:
			marketplace_id = self.marketplace_id

		append_to_base_uri = f"items/{seller_id}/{sku}"
		params = {
			"marketplaceIds": marketplace_id,
			"issueLocale": "en_US",
			"includedData": included_data,
		}

		return self.make_request(append_to_base_uri=append_to_base_uri, params=params)

	def fetch_for_sku(self, sku_name):
		try:
			response = retry_with_backoff(
				lambda: self.get_listing_item(sku=sku_name),
				error_title=f"Listings API fetch failed for SKU {sku_name}",
			)
		except Exception:
			return None

		summaries = response.get("summaries") or []
		if not summaries:
			return None
		return summaries[0]

	# Used for Bulk Fetch of CUrrent Price Listing
	def search_items(
		self,
		included_data: str = "summaries",
		page_size: int = 20,
		marketplace_id: str | None = None,
	) -> list:
		# Bulk pagination instead of one get_listing_item per SKU, which throttles on large catalogues.
		if not marketplace_id:
			marketplace_id = self.marketplace_id

		append_to_base_uri = f"items/{self.seller_id}"
		items = []
		page_token = None
		while True:
			params = {
				"marketplaceIds": marketplace_id,
				"issueLocale": "en_US",
				"includedData": included_data,
				"pageSize": page_size,
				"pageToken": page_token,
			}
			response = retry_with_backoff(
				lambda params=params: self.make_request(append_to_base_uri=append_to_base_uri, params=params),
				error_title="Listings searchItems failed",
			)
			items.extend(response.get("items") or [])
			page_token = (response.get("pagination") or {}).get("nextToken")
			if not page_token:
				break
		return items

	BY_SKU_BATCH_SIZE = 20

	def search_items_by_skus(
		self,
		skus: list[str],
		included_data: str = "attributes",
		marketplace_id: str | None = None,
	) -> list:
		# Unfiltered searchItems hard-caps at 1000 results; querying by explicit identifiers
		# (max 20 per call) is the only way to cover the full catalogue.
		if not marketplace_id:
			marketplace_id = self.marketplace_id

		append_to_base_uri = f"items/{self.seller_id}"
		items = []
		for start in range(0, len(skus), self.BY_SKU_BATCH_SIZE):
			batch = skus[start : start + self.BY_SKU_BATCH_SIZE]
			params = {
				"marketplaceIds": marketplace_id,
				"identifiers": ",".join(batch),
				"identifiersType": "SKU",
				"issueLocale": "en_US",
				"includedData": included_data,
				"pageSize": self.BY_SKU_BATCH_SIZE,
			}
			response = retry_with_backoff(
				lambda params=params: self.make_request(append_to_base_uri=append_to_base_uri, params=params),
				error_title="Listings searchItems by SKU failed",
			)
			items.extend(response.get("items") or [])
		return items


class FBAInventory(SPAPI):
	"""Amazon FBA Inventory API"""

	BASE_URI = "/fba/inventory/v1/"
	BATCH_SIZE = 50
	DELAY_BETWEEN_REQUESTS = 0.5

	def get_inventory_summaries(
		self,
		seller_skus: list[str],
		marketplace_id: str | None = None,
		next_token: str | None = None,
	) -> dict:
		if next_token:
			return self.make_request(
				append_to_base_uri="summaries",
				params={"nextToken": next_token},
			)

		if not marketplace_id:
			marketplace_id = self.marketplace_id

		params = {
			"details": "true",
			"granularityType": "Marketplace",
			"granularityId": marketplace_id,
			"marketplaceIds": marketplace_id,
			"sellerSkus": ",".join(seller_skus),
		}

		return self.make_request(append_to_base_uri="summaries", params=params)

	@staticmethod
	def parse_summary(summary):
		return {
			"asin": summary.get("asin"),
			"fnSku": summary.get("fnSku"),
			"condition": summary.get("condition"),
			"fulfillableQuantity": (summary.get("inventoryDetails") or {}).get("fulfillableQuantity", 0),
			"reservedQuantity": ((summary.get("inventoryDetails") or {}).get("reservedQuantity") or {}).get(
				"totalReservedQuantity", 0
			),
			"inboundWorkingQuantity": (
				(summary.get("inventoryDetails") or {}).get("researchingQuantity") or {}
			).get("totalResearchingQuantity", 0),
		}

	def fetch_for_skus(self, seller_skus):
		results = {}

		for batch_start in range(0, len(seller_skus), self.BATCH_SIZE):
			batch = seller_skus[batch_start : batch_start + self.BATCH_SIZE]

			try:
				response = retry_with_backoff(
					lambda b=batch: self.get_inventory_summaries(seller_skus=b),
					error_title=f"FBA Inventory fetch failed for batch starting at {batch_start}",
				)
			except Exception:
				continue

			for summary in (response.get("payload") or {}).get("inventorySummaries") or []:
				results[summary.get("sellerSku")] = self.parse_summary(summary)

			next_token = (response.get("payload") or {}).get("nextToken")
			while next_token:
				time.sleep(self.DELAY_BETWEEN_REQUESTS)
				token = next_token

				try:
					response = retry_with_backoff(
						lambda t=token: self.get_inventory_summaries(seller_skus=[], next_token=t),
						error_title="FBA Inventory pagination fetch failed",
					)
				except Exception:
					break

				for summary in (response.get("payload") or {}).get("inventorySummaries") or []:
					results[summary.get("sellerSku")] = self.parse_summary(summary)
				next_token = (response.get("payload") or {}).get("nextToken")

			if batch_start + self.BATCH_SIZE < len(seller_skus):
				time.sleep(self.DELAY_BETWEEN_REQUESTS)

		return results


class Reports(SPAPI):
	BASE_URI = "/reports/2021-06-30/"
	POLL_INTERVAL = 20
	MAX_POLL_ATTEMPTS = 30

	def create_report(self, report_type, marketplace_ids=None, data_start_time=None, data_end_time=None):
		if not marketplace_ids:
			marketplace_ids = [self.marketplace_id]

		body = {
			"reportType": report_type,
			"marketplaceIds": marketplace_ids,
		}
		if data_start_time:
			body["dataStartTime"] = data_start_time
		if data_end_time:
			body["dataEndTime"] = data_end_time

		url = self.endpoint + self.BASE_URI + "reports"
		result = frappe_make_request(
			method="POST",
			url=url,
			json=body,
			headers={**self.get_headers(), "Content-Type": "application/json"},
		)
		return result.get("reportId")

	def get_report(self, report_id):
		return self.make_request(append_to_base_uri=f"reports/{report_id}")

	def get_report_document(self, report_document_id):
		return self.make_request(append_to_base_uri=f"documents/{report_document_id}")

	def download_report_file(self, url, compression=None):
		content = download_binary(url)
		if compression and compression.upper() == "GZIP":
			content = gzip.decompress(content)

		try:
			return content.decode("utf-8")
		except UnicodeDecodeError:
			return content.decode("cp1252")

	def request_and_download_report(self, report_type, data_start_time=None, data_end_time=None):
		report_id = retry_with_backoff(
			lambda: self.create_report(
				report_type, data_start_time=data_start_time, data_end_time=data_end_time
			),
			error_title=f"Reports API: failed to create {report_type}",
		)

		if not report_id:
			frappe.throw(f"Failed to create report: {report_type}")

		report_document_id = None
		for _attempt in range(self.MAX_POLL_ATTEMPTS):
			time.sleep(self.POLL_INTERVAL)

			report_status = retry_with_backoff(
				lambda rid=report_id: self.get_report(rid),
				error_title=f"Reports API: failed to poll report {report_id}",
			)

			status = report_status.get("processingStatus")
			if status == "DONE":
				report_document_id = report_status.get("reportDocumentId")
				break
			elif status in ("CANCELLED", "FATAL"):
				frappe.throw(f"Report {report_id} failed with status: {status}")

		if not report_document_id:
			frappe.throw(f"Report {report_id} timed out after {self.MAX_POLL_ATTEMPTS * self.POLL_INTERVAL}s")

		document = retry_with_backoff(
			lambda: self.get_report_document(report_document_id),
			error_title=f"Reports API: failed to get document {report_document_id}",
		)

		download_url = document.get("url")
		compression = document.get("compressionAlgorithm")

		return self.download_report_file(download_url, compression)


class Feeds(SPAPI):
	"""
	Amazon Feeds API for pricing submissions.

	The Feeds API lets you upload information to Amazon on behalf of a selling partner.
	This is used to submit pricing updates to Amazon.

	Flow:
	1. Create a feed document (get upload URL)
	2. Upload feed content to the URL
	3. Create the feed (submit for processing)
	4. Poll feed status until complete
	"""

	BASE_URI = "/feeds/2021-06-30/"

	def post_feed_json(self, url: str, payload: dict, error_code: str, error_label: str) -> dict:
		# Retry only on Amazon throttling (429/503); any other error fails fast. status_code is
		# surfaced on SPAPIError so retry_with_backoff can tell a throttle from a real failure.
		def request():
			try:
				return frappe_make_request(
					method="POST",
					url=url,
					json=payload,
					headers={**self.get_headers(), "Content-Type": "application/json"},
				)
			except HTTPError as error:
				status_code = error.response.status_code if error.response is not None else None
				raise SPAPIError(
					error=error_code,
					error_description=f"{error_label}: {error!s}",
					status_code=status_code,
				) from error
			except Exception as error:
				raise SPAPIError(error=error_code, error_description=f"{error_label}: {error!s}") from error

		return retry_with_backoff(
			request,
			error_title=error_label,
			max_retries=FEED_MAX_RETRIES,
			throttle_only=True,
		)

	def create_feed_document(self, content_type: str = "application/json") -> dict:
		"""
		Create a feed document and get a pre-signed URL for upload.

		Args:
			content_type: Content type of the feed (default: application/json)

		Returns:
			Dictionary containing:
			- feedDocumentId: The feed document identifier
			- url: Pre-signed URL for uploading content
		"""
		block_if_amazon_frozen()
		block_if_pricing_not_approved()
		url = self.endpoint + self.BASE_URI + "documents"

		return self.post_feed_json(
			url,
			{"contentType": content_type},
			error_code="CreateFeedDocumentError",
			error_label="Failed to create feed document",
		)

	def upload_feed_content(
		self, url: str, content: str | bytes, content_type: str = "application/json"
	) -> bool:
		"""
		Upload feed content to the pre-signed URL (S3).

		Args:
			url: Pre-signed URL from create_feed_document
			content: The feed content to upload (JSON string or bytes)
			content_type: Content type of the feed

		"""
		if isinstance(content, str):
			content = content.encode("utf-8")

		try:
			upload_binary(url, content, content_type)
			return True
		except Exception as e:
			raise SPAPIError(
				error="UploadFeedError", error_description=f"Failed to upload feed content: {e!s}"
			)

	def create_feed(self, feed_type: str, feed_document_id: str, marketplace_ids: list | None = None) -> dict:
		"""
		Submit the feed for processing.

		Args:
			feed_type: The type of feed (e.g., JSON_LISTINGS_FEED)
			feed_document_id: The feed document ID from create_feed_document
			marketplace_ids: List of marketplace IDs (uses default if not provided)

		Returns:
			Dictionary containing:
			- feedId: The feed identifier for tracking
		"""
		block_if_amazon_frozen()
		block_if_pricing_not_approved()
		if not marketplace_ids:
			marketplace_ids = [self.marketplace_id]

		url = self.endpoint + self.BASE_URI + "feeds"

		return self.post_feed_json(
			url,
			{
				"feedType": feed_type,
				"marketplaceIds": marketplace_ids,
				"inputFeedDocumentId": feed_document_id,
			},
			error_code="CreateFeedError",
			error_label="Failed to create feed",
		)

	def get_feed(self, feed_id: str) -> dict:
		"""
		Get the status of a feed.

		Args:
			feed_id: The feed ID from create_feed

		Returns:
			Dictionary containing feed status and details
		"""
		append_to_base_uri = f"feeds/{feed_id}"
		return self.make_request(append_to_base_uri=append_to_base_uri)

	def get_feed_document(self, feed_document_id: str) -> dict:
		"""
		Get feed document details including download URL for results.

		Args:
			feed_document_id: The feed document ID

		Returns:
			Dictionary containing document details and download URL
		"""
		append_to_base_uri = f"documents/{feed_document_id}"
		return self.make_request(append_to_base_uri=append_to_base_uri)

	def get_feeds(
		self,
		feed_types: list | None = None,
		marketplace_ids: list | None = None,
		page_size: int = 10,
		processing_statuses: list | None = None,
		created_since: str | None = None,
		created_until: str | None = None,
		next_token: str | None = None,
	) -> dict:
		"""
		List feeds with optional filters.

		Args:
			feed_types: Filter by feed types
			marketplace_ids: Filter by marketplace IDs
			page_size: Number of results per page (max 100)
			processing_statuses: Filter by status (IN_QUEUE, IN_PROGRESS, DONE, CANCELLED, FATAL)
			created_since: ISO datetime string
			created_until: ISO datetime string
			next_token: Token for pagination

		Returns:
			Dictionary containing feeds list and pagination info
		"""
		# Amazon rejects nextToken combined with any other query parameter, so on pagination
		# send it alone. The array params (feedTypes/marketplaceIds/processingStatuses) must be
		# comma-separated values — indexed notation (feedTypes[0]=…) is rejected with HTTP 400.
		if next_token:
			data = {"nextToken": next_token}
		else:
			data = {
				"pageSize": page_size,
				"createdSince": created_since,
				"createdUntil": created_until,
				"feedTypes": ",".join(feed_types) if feed_types else None,
				"marketplaceIds": ",".join(marketplace_ids or [self.marketplace_id]),
			}
			if processing_statuses:
				data["processingStatuses"] = ",".join(processing_statuses)

		return self.make_request(append_to_base_uri="feeds", params=data)


class FulfillmentInbound(SPAPI):
	BASE_URI = "/inbound/fba/2024-03-20/"
	POLL_INTERVAL = 5
	MAX_POLL_ATTEMPTS = 10

	def create_inbound_plan(self, name, source_address, destination_marketplaces, items):
		return self.make_request(
			method="POST",
			append_to_base_uri="inboundPlans",
			json={
				"name": name,
				"sourceAddress": source_address,
				"destinationMarketplaces": destination_marketplaces,
				"items": items,
			},
		)

	def get_inbound_plan(self, inbound_plan_id):
		return self.make_request(append_to_base_uri=f"inboundPlans/{inbound_plan_id}")

	def cancel_inbound_plan(self, inbound_plan_id):
		return self.make_request(
			method="PUT", append_to_base_uri=f"inboundPlans/{inbound_plan_id}/cancellation"
		)

	def get_operation(self, operation_id):
		return self.make_request(append_to_base_uri=f"operations/{operation_id}")

	def poll_operation(self, operation_id):
		for attempt in range(self.MAX_POLL_ATTEMPTS):
			result = self.get_operation(operation_id)
			status = result.get("operationStatus")
			if status == "SUCCESS":
				return result
			if status == "FAILED":
				problems = result.get("operationProblems", [])
				errors = [p for p in problems if p.get("severity") == "ERROR"]
				if errors:
					raise SPAPIError(
						error="OperationFailed",
						error_description=f"Operation {operation_id} failed: {problems}",
					)
				return result
			time.sleep(self.POLL_INTERVAL * (2**attempt))

		raise SPAPIError(
			error="OperationTimeout",
			error_description=f"Operation {operation_id} did not complete after {self.MAX_POLL_ATTEMPTS} attempts",
		)

	def generate_packing_options(self, inbound_plan_id):
		return self.make_request(
			method="POST",
			append_to_base_uri=f"inboundPlans/{inbound_plan_id}/packingOptions",
			json={},
		)

	def list_packing_options(self, inbound_plan_id):
		return self.make_request(append_to_base_uri=f"inboundPlans/{inbound_plan_id}/packingOptions")

	def confirm_packing_option(self, inbound_plan_id, packing_option_id):
		return self.make_request(
			method="POST",
			append_to_base_uri=f"inboundPlans/{inbound_plan_id}/packingOptions/{packing_option_id}/confirmation",
			json={},
		)

	def set_packing_information(self, inbound_plan_id, body):
		return self.make_request(
			method="POST",
			append_to_base_uri=f"inboundPlans/{inbound_plan_id}/packingInformation",
			json=body,
		)

	def generate_placement_options(self, inbound_plan_id):
		return self.make_request(
			method="POST",
			append_to_base_uri=f"inboundPlans/{inbound_plan_id}/placementOptions",
			json={},
		)

	def list_placement_options(self, inbound_plan_id):
		return self.make_request(append_to_base_uri=f"inboundPlans/{inbound_plan_id}/placementOptions")

	def confirm_placement_option(self, inbound_plan_id, placement_option_id):
		return self.make_request(
			method="POST",
			append_to_base_uri=f"inboundPlans/{inbound_plan_id}/placementOptions/{placement_option_id}/confirmation",
			json={},
		)

	def get_shipment(self, inbound_plan_id, shipment_id):
		return self.make_request(append_to_base_uri=f"inboundPlans/{inbound_plan_id}/shipments/{shipment_id}")

	def list_shipment_items(self, inbound_plan_id, shipment_id):
		return self.make_request(
			append_to_base_uri=f"inboundPlans/{inbound_plan_id}/shipments/{shipment_id}/items"
		)

	def list_shipment_boxes(self, inbound_plan_id, shipment_id):
		return self.make_request(
			append_to_base_uri=f"inboundPlans/{inbound_plan_id}/shipments/{shipment_id}/boxes"
		)

	def generate_transportation_options(self, inbound_plan_id, body):
		return self.make_request(
			method="POST",
			append_to_base_uri=f"inboundPlans/{inbound_plan_id}/transportationOptions",
			json=body,
		)

	def list_transportation_options(self, inbound_plan_id, placement_option_id=None, shipment_id=None):
		params = {}
		if placement_option_id:
			params["placementOptionId"] = placement_option_id
		if shipment_id:
			params["shipmentId"] = shipment_id
		return self.make_request(
			append_to_base_uri=f"inboundPlans/{inbound_plan_id}/transportationOptions",
			params=params or None,
		)

	def confirm_transportation_options(self, inbound_plan_id, body):
		return self.make_request(
			method="POST",
			append_to_base_uri=f"inboundPlans/{inbound_plan_id}/transportationOptions/confirmation",
			json=body,
		)

	def list_prep_details(self, msku_list, marketplace_id):
		params = [("marketplaceId", marketplace_id)]
		for msku in msku_list:
			params.append(("mskus", msku))

		return self.make_request(append_to_base_uri="items/prepDetails", params=params)

	def get_labels_v0(self, shipment_confirmation_id, page_type="PackageLabel_Plain_Paper", page_size=1):
		url = self.endpoint + f"/fba/inbound/v0/shipments/{shipment_confirmation_id}/labels"
		params = {"PageType": page_type, "PageSize": page_size}
		try:
			result = make_get_request(url=url, params=params, headers=self.get_headers())
			return result.get("payload", result)
		except SPAPIError:
			raise
		except Exception as e:
			raise SPAPIError(
				error="Failed to get labels",
				error_description=cstr(e),
			)

	def download_label_pdf(self, url):
		return download_binary(url, timeout=60)

	def update_shipment_tracking(self, inbound_plan_id, shipment_id, body):
		return self.make_request(
			method="PUT",
			append_to_base_uri=f"inboundPlans/{inbound_plan_id}/shipments/{shipment_id}/trackingDetails",
			json=body,
		)

	def generate_delivery_window_options(self, shipment_id):
		return self.make_request(
			method="POST",
			append_to_base_uri=f"shipments/{shipment_id}/deliveryWindowOptions",
			json={},
		)

	def list_delivery_window_options(self, shipment_id):
		return self.make_request(append_to_base_uri=f"shipments/{shipment_id}/deliveryWindowOptions")

	def confirm_delivery_window(self, shipment_id, delivery_window_option_id):
		return self.make_request(
			method="POST",
			append_to_base_uri=f"shipments/{shipment_id}/deliveryWindowOptions/{delivery_window_option_id}/confirmation",
			json={},
		)
