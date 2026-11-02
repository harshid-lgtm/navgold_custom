import hashlib
import json
import time

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cstr, get_request_session


DEFAULT_TEMU_EU_ENDPOINT = "https://openapi-b-eu.temu.com/openapi/router"


class TemuAPISettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		access_token: DF.Password
		api_endpoint: DF.Data
		app_key: DF.Data
		app_secret: DF.Password
		company: DF.Link | None
		customer_group: DF.Link | None
		is_active: DF.Check
		last_order_id: DF.Data | None
		last_order_response: DF.Code | None
		territory: DF.Link | None
		warehouse: DF.Link | None
	# end: auto-generated types

	def validate(self):
		if not self.api_endpoint:
			self.api_endpoint = DEFAULT_TEMU_EU_ENDPOINT

	@frappe.whitelist(methods=["POST"])
	def fetch_order(self, parent_order_sn: str):
		frappe.only_for("System Manager")
		if not self.is_active:
			frappe.throw(_("Please enable Temu API Settings before fetching an order."))

		parent_order_sn = cstr(parent_order_sn).strip()
		if not parent_order_sn:
			frappe.throw(_("Please enter a Temu Order ID."))

		response = TemuClient.from_settings(self).get_order(parent_order_sn)

		self.db_set(
			{
				"last_order_id": parent_order_sn,
				"last_order_response": frappe.as_json(response, indent=2),
			},
			update_modified=False,
		)

		return response


class TemuClient:
	def __init__(self, app_key: str, app_secret: str, access_token: str, endpoint: str):
		self.app_key = app_key
		self.app_secret = app_secret
		self.access_token = access_token
		self.endpoint = endpoint or DEFAULT_TEMU_EU_ENDPOINT

	@classmethod
	def from_settings(cls, settings):
		return cls(
			app_key=settings.app_key,
			app_secret=settings.get_password("app_secret"),
			access_token=settings.get_password("access_token"),
			endpoint=settings.api_endpoint,
		)

	def get_order(self, parent_order_sn: str) -> dict:
		return self.call(
			"bg.order.list.v2.get",
			{
				"parentOrderSnList": [parent_order_sn],
				"pageNumber": 1,
				"pageSize": 10,
			},
		)

	def call(self, api_type: str, params: dict | None = None) -> dict:
		payload = {
			"app_key": self.app_key,
			"access_token": self.access_token,
			"type": api_type,
			"timestamp": int(time.time()),
			"data_type": "JSON",
		}
		payload.update(params or {})
		payload["sign"] = generate_temu_signature(payload, self.app_secret)

		session = get_request_session()
		response = session.post(
			self.endpoint,
			json=payload,
			headers={"Content-Type": "application/json"},
			timeout=60,
		)
		response.raise_for_status()
		result = response.json()

		if not result.get("success"):
			frappe.throw(
				_("Temu API error {0}: {1}").format(
					result.get("errorCode") or _("Unknown"),
					result.get("errorMsg") or _("Unknown error"),
				)
			)

		return result


def serialize_sign_value(value) -> str:
	if isinstance(value, bool):
		return "true" if value else "false"
	if isinstance(value, (dict, list)):
		return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
	if value is None:
		return ""
	return str(value)


def generate_temu_signature(payload: dict, app_secret: str) -> str:
	parts = []
	for key in sorted(payload):
		if key == "sign":
			continue
		parts.append(f"{key}{serialize_sign_value(payload[key])}")

	signature_source = f"{app_secret}{''.join(parts)}{app_secret}"
	return hashlib.md5(signature_source.encode("utf-8")).hexdigest().upper()
