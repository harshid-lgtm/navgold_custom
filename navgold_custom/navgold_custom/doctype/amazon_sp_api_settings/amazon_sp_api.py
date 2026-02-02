# Attribution: Code adapted from eFeone's eSeller Suite (https://github.com/efeone/eseller_suite)
# Original work licensed under the MIT License
# Copyright (c) 2024, eFeone and contributors
#
# Modifications and additions
# Copyright (c) 2025, BWH Studios and contributors
# For license information, please see license.txt
from frappe.integrations.utils import make_request as frappe_make_request
from frappe.utils import get_request_session

# https://github.com/amzn/selling-partner-api-docs/blob/main/guides/en-US/developer-guide/SellingPartnerApiDeveloperGuide.md#selling-partner-api-endpoints
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
		super().__init__(*args)


class SPAPI:
	"""Base Amazon SP-API class"""

	# https://github.com/amzn/selling-partner-api-docs/blob/main/guides/en-US/developer-guide/SellingPartnerApiDeveloperGuide.md#connecting-to-the-selling-partner-api
	AUTH_URL = "https://api.amazon.com/auth/o2/token"

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

	def get_access_token(self) -> str:
		data = {
			"grant_type": "refresh_token",
			"client_id": self.client_id,
			"client_secret": self.client_secret,
			"refresh_token": self.refresh_token,
		}

		try:
			result = frappe_make_request("POST", self.AUTH_URL, data=data)
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
			)

	def get_headers(self) -> dict:
		return {"x-amz-access-token": self.get_access_token()}

	def make_request(
		self,
		method: str = "GET",
		append_to_base_uri: str = "",
		params: dict | None = None,
		data: dict | None = None,
	) -> dict:
		if isinstance(params, dict):
			params = Util.remove_empty(params)
		if isinstance(data, dict):
			data = Util.remove_empty(data)

		url = self.endpoint + self.BASE_URI + append_to_base_uri

		return frappe_make_request(
			method=method, url=url, params=params, data=data, headers=self.get_headers()
		)

	def list_to_dict(self, key: str, values: list, data: dict) -> None:
		if values and isinstance(values, list):
			for idx in range(len(values)):
				data[f"{key}[{idx}]"] = values[idx]


class Finances(SPAPI):
	"""Amazon Finances API"""

	BASE_URI = "/finances/v0/"

	def list_financial_events_by_order_id(
		self, order_id: str, max_results: int | None = None, next_token: str | None = None
	) -> dict:
		"""Returns all financial events for the specified order."""
		append_to_base_uri = f"orders/{order_id}/financialEvents"
		data = dict(MaxResultsPerPage=max_results, NextToken=next_token)
		return self.make_request(append_to_base_uri=append_to_base_uri, params=data)


class Orders(SPAPI):
	"""Amazon Orders API"""

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


class CatalogItems(SPAPI):
	"""Amazon Catalog Items API"""

	# BASE_URI = "/catalog/v0"
	BASE_URI = "/catalog/2022-04-01"

	def get_catalog_item(
		self,
		asin: str,
		marketplace_id: str | None = None,
	) -> dict:
		"""Returns a specified item and its attributes."""
		if not marketplace_id:
			marketplace_id = self.marketplace_id

		append_to_base_uri = f"/items/{asin}"
		data = dict(marketplaceIds=marketplace_id)
		# append_to_base_uri = f"?marketplaceIds={marketplace_id}"
		data
		response_from_reqeust = self.make_request(append_to_base_uri=append_to_base_uri, params=data)
		return response_from_reqeust


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
		"""
		Helper function that removes all keys from a dictionary (dict), that have an empty value.
		"""
		for key in list(dict):
			if not dict[key]:
				del dict[key]
		return dict


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
		url = self.endpoint + self.BASE_URI + "documents"

		try:
			result = frappe_make_request(
				method="POST",
				url=url,
				json={"contentType": content_type},
				headers={**self.get_headers(), "Content-Type": "application/json"},
			)
			return result
		except Exception as e:
			raise SPAPIError(
				error="CreateFeedDocumentError", error_description=f"Failed to create feed document: {e!s}"
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

		Returns:
			True if upload successful
		"""
		if isinstance(content, str):
			content = content.encode("utf-8")

		try:
			# Use Frappe's request session for S3 upload
			session = get_request_session()
			response = session.put(url=url, data=content, headers={"Content-Type": content_type})
			response.raise_for_status()
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
		if not marketplace_ids:
			marketplace_ids = [self.marketplace_id]

		url = self.endpoint + self.BASE_URI + "feeds"

		try:
			result = frappe_make_request(
				method="POST",
				url=url,
				json={
					"feedType": feed_type,
					"marketplaceIds": marketplace_ids,
					"inputFeedDocumentId": feed_document_id,
				},
				headers={**self.get_headers(), "Content-Type": "application/json"},
			)
			return result
		except Exception as e:
			raise SPAPIError(error="CreateFeedError", error_description=f"Failed to create feed: {e!s}")

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
		data = {
			"pageSize": page_size,
			"nextToken": next_token,
			"createdSince": created_since,
			"createdUntil": created_until,
		}

		self.list_to_dict("feedTypes", feed_types, data)
		self.list_to_dict("marketplaceIds", marketplace_ids or [self.marketplace_id], data)
		self.list_to_dict("processingStatuses", processing_statuses, data)

		append_to_base_uri = "feeds"
		return self.make_request(append_to_base_uri=append_to_base_uri, params=data)
