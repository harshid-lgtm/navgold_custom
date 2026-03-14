# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import base64

import frappe
from erpnext.stock.doctype.delivery_note.delivery_note import make_packing_slip
from erpnext.stock.doctype.item.item import get_uom_conv_factor
from frappe import _
from frappe.utils import flt
from frappe.utils.data import cstr

from navgold_custom.navgold_custom.custom_script.packing_slip.packing_utils import (
	get_item_weight_map,
	recalculate_net_weight,
)
from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import (
	Orders,
	Shipping,
)


def before_validate(doc, method):
	recalculate_net_weight(doc)
	validate_packed_qty(doc)


def validate_packed_qty(doc):
	for item in doc.items:
		packed_qty = flt(item.custom_packed_qty)
		qty = flt(item.qty)
		if packed_qty != qty:
			frappe.throw(
				_("Row {0}: Packed Qty ({1}) does not match Required Qty ({2}) for item {3}").format(
					item.idx, packed_qty, qty, item.item_code
				)
			)


@frappe.whitelist()
def get_draft_delivery_note_items(tote: str | None = None):
	if not tote:
		return []

	pick_list_names = frappe.get_all(
		"Pick List",
		filters={"custom_tote": tote, "docstatus": 1, "status": "Open"},
		pluck="name",
	)

	if not pick_list_names:
		return []

	sales_orders = set(
		frappe.get_all(
			"Pick List Item",
			filters={"parent": ["in", pick_list_names]},
			pluck="sales_order",
		)
	)
	sales_orders.discard(None)

	if not sales_orders:
		return []

	delivery_note_items = frappe.get_all(
		"Delivery Note Item",
		filters={
			"docstatus": 0,
			"against_sales_order": ["in", sales_orders],
		},
		fields=["item_code", "item_name", "against_sales_order", "qty", "packed_qty", "parent"],
	)

	return [
		{
			"item_code": item.item_code,
			"item_name": item.item_name,
			"delivery_note": item.parent,
			"sales_order": item.against_sales_order,
		}
		for item in delivery_note_items
		if flt(item.packed_qty) < flt(item.qty)
	]


@frappe.whitelist()
def get_delivery_note_items(delivery_note: str):
	packing_slip_doc = make_packing_slip(delivery_note)
	item_codes = {item.item_code for item in packing_slip_doc.items}
	item_weights = get_item_weight_map(item_codes)

	return [
		{
			"item_code": item.item_code,
			"item_name": item.item_name,
			"description": item.description,
			"qty": item.qty,
			"uom": item.stock_uom,
			"dn_detail": item.dn_detail,
			"weight_per_unit": flt(item_weights.get(item.item_code, {}).get("weight_per_unit")),
			"weight_uom": item_weights.get(item.item_code, {}).get("weight_uom", ""),
		}
		for item in packing_slip_doc.items
	]


@frappe.whitelist()
def get_sfp_shipping_rates(packing_slip: str):
	packing_slip_doc = frappe.get_cached_doc("Packing Slip", packing_slip)
	shipping_payload = build_shipping_payload(packing_slip_doc)
	shipping = Shipping.from_amazon_settings()

	response = shipping.get_shipping_rates(payload=shipping_payload)
	rates_payload = response.get("payload")
	rates = rates_payload.get("rates") or []
	if not rates:
		frappe.throw(_("No shipping rates returned."))

	request_token = rates_payload.get("requestToken") or ""

	formatted_rates = []
	for rate in rates:
		if not rate.get("rateId"):
			continue
		document_spec = build_document_spec_from_rate(rate)
		formatted_rates.append(
			{
				"rate_id": rate.get("rateId"),
				"carrier_name": rate.get("carrierName"),
				"service_name": rate.get("serviceName"),
				"total_charge": (rate.get("totalCharge") or {}).get("value"),
				"currency": (rate.get("totalCharge") or {}).get("unit"),
				"delivery_window_start": ((rate.get("promise") or {}).get("deliveryWindow") or {}).get(
					"start"
				),
				"delivery_window_end": ((rate.get("promise") or {}).get("deliveryWindow") or {}).get("end"),
				"document_spec": document_spec,
				"request_token": request_token,
				"value_added_services": build_required_vas(rate),
			}
		)
	return formatted_rates


@frappe.whitelist()
def purchase_sfp_shipping_label(
	packing_slip: str,
	rate_id: str,
	document_spec: str | dict | None = None,
	request_token: str = "",
	value_added_services: str | list | None = None,
):
	if not rate_id:
		frappe.throw(_("Rate ID is required to purchase SFP label."))

	if isinstance(document_spec, str):
		document_spec = frappe.parse_json(document_spec)
	if isinstance(value_added_services, str):
		value_added_services = frappe.parse_json(value_added_services)

	if not document_spec:
		document_spec = {
			"format": "PDF",
			"size": {"length": 6.0, "unit": "INCH", "width": 4.0},
			"dpi": 300,
			"pageLayout": "DEFAULT",
			"needFileJoining": False,
			"requestedDocumentTypes": ["LABEL"],
		}

	packing_slip_doc = frappe.get_cached_doc("Packing Slip", packing_slip)
	shipping = Shipping.from_amazon_settings()

	response = shipping.purchase_shipment(
		rate_id=rate_id,
		request_token=request_token,
		document_spec=document_spec,
		value_added_services=value_added_services or [],
	)
	purchase_payload = response.get("payload")
	shipment_id = purchase_payload.get("shipmentId")
	if not shipment_id:
		frappe.throw(_("Shipment ID missing after purchasing label."))

	label_content, label_format, tracking_id = extract_label_from_purchase(purchase_payload)
	if not label_content:
		frappe.throw(_("Label document not found in purchase response for shipment {0}.").format(shipment_id))

	file_extension = "pdf" if label_format == "PDF" else label_format.lower()
	file_name = f"SFP-{packing_slip_doc.name}-{shipment_id}.{file_extension}"
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": "Packing Slip",
			"attached_to_name": packing_slip_doc.name,
			"content": label_content,
			"is_private": 0,
		}
	)
	file_doc.insert(ignore_permissions=True)
	file_url = file_doc.file_url
	packing_slip_doc.custom_sfp_rate_id = rate_id
	packing_slip_doc.custom_sfp_shipment_id = shipment_id
	packing_slip_doc.custom_sfp_label = file_url
	if tracking_id:
		packing_slip_doc.custom_sfp_tracking_id = tracking_id
	packing_slip_doc.save(ignore_permissions=True)

	return {
		"file_url": file_url,
		"rate_id": rate_id,
		"shipment_id": shipment_id,
		"tracking_id": tracking_id,
	}


@frappe.whitelist()
def cancel_sfp_shipment(packing_slip: str):
	packing_slip_doc = frappe.get_cached_doc("Packing Slip", packing_slip)
	if not packing_slip_doc.custom_sfp_shipment_id:
		frappe.throw(_("No SFP shipment to cancel."))

	shipping = Shipping.from_amazon_settings()
	shipping.cancel_shipment(shipment_id=packing_slip_doc.custom_sfp_shipment_id)
	packing_slip_doc.custom_sfp_rate_id = ""
	packing_slip_doc.custom_sfp_shipment_id = ""
	packing_slip_doc.custom_sfp_label = ""
	packing_slip_doc.custom_sfp_tracking_id = ""
	packing_slip_doc.save(ignore_permissions=True)


# VAS = Value Added Services
def build_required_vas(rate: dict) -> list:
	vas_groups = rate.get("availableValueAddedServiceGroups") or []
	selected = []
	for group in vas_groups:
		if not group.get("isRequired"):
			continue
		services = group.get("valueAddedServices") or []
		if services:
			selected.append({"id": services[0].get("id")})
	return selected


# Use of base64 instead of Download URL because label is encoded directly in the response
def extract_label_from_purchase(purchase_payload: dict) -> tuple:
	package_details = (purchase_payload or {}).get("packageDocumentDetails") or []
	for package in package_details:
		tracking_id = cstr(package.get("trackingId") or "")
		documents = package.get("packageDocuments") or []
		for document in documents:
			if document.get("type") == "LABEL" and document.get("contents"):
				label_bytes = base64.b64decode(document["contents"])
				label_format = document.get("format", "PDF")
				return label_bytes, label_format, tracking_id

	return None, None, None


def build_document_spec_from_rate(rate: dict) -> dict:
	specs = rate.get("supportedDocumentSpecifications") or []
	pdf_spec = None
	for spec in specs:
		if spec.get("format") == "PDF":
			pdf_spec = spec
			break

	if not pdf_spec:
		pdf_spec = specs[0] if specs else {}

	if not pdf_spec:
		frappe.throw(_("No document specifications returned by Amazon for this rate."))

	print_options = (pdf_spec.get("printOptions") or [{}])[0]
	dpis = print_options.get("supportedDPIs") or []
	page_layouts = print_options.get("supportedPageLayouts") or []
	file_joining = print_options.get("supportedFileJoiningOptions") or [False]
	document_types = [
		detail.get("name")
		for detail in (print_options.get("supportedDocumentDetails") or [])
		if detail.get("isMandatory")
	]

	result = {
		"format": pdf_spec.get("format", "PDF"),
		"size": pdf_spec.get("size", {"length": 6.0, "unit": "INCH", "width": 4.0}),
		"needFileJoining": file_joining[0] if file_joining else False,
		"requestedDocumentTypes": document_types or ["LABEL"],
	}
	if dpis:
		result["dpi"] = dpis[0]
	if page_layouts:
		result["pageLayout"] = page_layouts[0]

	return result


def build_ship_to_address(shipping_address: dict, buyer_info: dict, amazon_order_id: str) -> dict:
	ship_to = {
		"name": shipping_address.get("Name"),
		"addressLine1": shipping_address.get("AddressLine1"),
		"city": shipping_address.get("City"),
		"postalCode": shipping_address.get("PostalCode"),
		"countryCode": shipping_address.get("CountryCode"),
	}
	if shipping_address.get("AddressLine2"):
		ship_to["addressLine2"] = shipping_address.get("AddressLine2")
	if shipping_address.get("StateOrRegion"):
		ship_to["stateOrRegion"] = shipping_address.get("StateOrRegion")

	phone_number = (
		shipping_address.get("Phone") or buyer_info.get("BuyerPhoneNumber") or buyer_info.get("BuyerPhone")
	)
	if phone_number:
		ship_to["phoneNumber"] = phone_number

	buyer_email = buyer_info.get("BuyerEmail") or ""
	if buyer_email:
		ship_to["email"] = buyer_email

	required = ["name", "addressLine1", "city", "postalCode", "countryCode"]
	if any(not ship_to.get(key) for key in required):
		frappe.throw(_("Shipping address is incomplete for Amazon Order {0}.").format(amazon_order_id))

	return ship_to


def get_amazon_order_id(delivery_note_name):
	sales_order_names = frappe.get_all(
		"Delivery Note Item",
		filters={"parent": delivery_note_name},
		pluck="against_sales_order",
	)
	sales_order_names = sorted({name for name in sales_order_names if name})

	if not sales_order_names:
		frappe.throw(_("No Sales Order found for Delivery Note {0}.").format(delivery_note_name))

	amazon_order_ids = frappe.get_all(
		"Sales Order",
		filters={"name": ["in", sales_order_names]},
		pluck="amazon_order_id",
	)
	amazon_order_ids = {order_id for order_id in amazon_order_ids if order_id}

	if len(amazon_order_ids) != 1:
		frappe.throw(
			_("Expected one Amazon Order ID for Delivery Note {0}, found {1}.").format(
				delivery_note_name, len(amazon_order_ids)
			)
		)

	return next(iter(amazon_order_ids))


def build_package_items(orders, amazon_order_id, currency, weight_in_grams):
	order_items = []
	next_token = None
	while True:
		response = orders.get_order_items(order_id=amazon_order_id, next_token=next_token)
		payload = response.get("payload")
		order_items.extend(payload.get("OrderItems") or [])
		next_token = payload.get("NextToken")
		if not next_token:
			break

	total_item_value = 0
	package_items = []
	for item in order_items:
		order_item_id = item.get("OrderItemId")
		if not order_item_id:
			continue
		quantity = max(1, int(flt(item.get("QuantityOrdered")) or 0))
		item_price = flt((item.get("ItemPrice") or {}).get("Amount")) or 1.0
		total_item_value += item_price
		package_item = {
			"itemIdentifier": order_item_id,
			"quantity": quantity,
			"itemValue": {"unit": currency, "value": round(item_price, 2)},
			"weight": {"unit": "GRAM", "value": round(weight_in_grams / max(len(order_items), 1), 2)},
		}
		description = item.get("Title") or ""
		if description:
			package_item["description"] = description[:256]
		package_items.append(package_item)

	return package_items, total_item_value


def build_shipping_payload(packing_slip_doc):
	if not packing_slip_doc.delivery_note:
		frappe.throw(_("Delivery Note is required to create SFP shipping label."))

	delivery_note_name = packing_slip_doc.delivery_note
	amazon_order_id = get_amazon_order_id(delivery_note_name)

	orders = Orders.from_amazon_settings()

	address_response = orders.get_order_address(order_id=amazon_order_id)
	address_payload = address_response.get("payload")
	shipping_address = address_payload.get("ShippingAddress") or {}
	if not shipping_address:
		frappe.throw(_("Shipping address not found for Amazon Order {0}.").format(amazon_order_id))

	buyer_response = orders.get_order_buyer_info(order_id=amazon_order_id)
	buyer_info = buyer_response.get("payload")

	settings = frappe.get_cached_doc("Navgold Settings")
	ship_from = settings.get_source_address(state_key="stateOrRegion")

	ship_to = build_ship_to_address(shipping_address, buyer_info, amazon_order_id)

	weight_value = flt(packing_slip_doc.gross_weight_pkg) or flt(packing_slip_doc.net_weight_pkg)
	weight_uom = packing_slip_doc.gross_weight_uom or packing_slip_doc.net_weight_uom or "KG"

	weight_in_grams = flt(weight_value) * flt(get_uom_conv_factor(weight_uom, "Gram"))
	if weight_in_grams <= 0:
		frappe.throw(_("Package weight is required to create SFP label."))

	package_count = 1
	if flt(packing_slip_doc.to_case_no) and flt(packing_slip_doc.from_case_no):
		package_count = int(flt(packing_slip_doc.to_case_no) - flt(packing_slip_doc.from_case_no) + 1)
		if package_count <= 0:
			package_count = 1

	depth_cm = flt(packing_slip_doc.custom_depth_cm)
	height_cm = flt(packing_slip_doc.custom_height_cm)
	width_cm = flt(packing_slip_doc.custom_width_cm)
	if not (depth_cm and height_cm and width_cm):
		frappe.throw(_("Package dimensions are required to create SFP label."))

	company_name = frappe.db.get_value("Delivery Note", delivery_note_name, "company")
	currency = (
		frappe.db.get_value("Company", company_name, "default_currency") if company_name else None
	) or "GBP"

	package_items, total_item_value = build_package_items(orders, amazon_order_id, currency, weight_in_grams)

	packages = []
	for index in range(package_count):
		packages.append(
			{
				"packageClientReferenceId": f"{packing_slip_doc.name}-{index + 1}",
				"weight": {"unit": "GRAM", "value": round(weight_in_grams, 2)},
				"dimensions": {
					"unit": "CENTIMETER",
					"length": round(depth_cm, 2),
					"width": round(width_cm, 2),
					"height": round(height_cm, 2),
				},
				"insuredValue": {"unit": currency, "value": round(total_item_value, 2)},
				"items": package_items,
			}
		)

	channel_details = {
		"channelType": "AMAZON",
		"amazonOrderDetails": {"orderId": amazon_order_id},
	}
	merchant_shipping_group = get_merchant_shipping_group(packing_slip_doc.items or [])
	if merchant_shipping_group:
		channel_details["merchantShippingGroupName"] = merchant_shipping_group

	return {
		"shipFrom": ship_from,
		"shipTo": ship_to,
		"packages": packages,
		"channelDetails": channel_details,
		"shipDate": frappe.utils.now_datetime().strftime("%Y-%m-%dT%H:%M:%SZ"),
	}


def get_merchant_shipping_group(items: list) -> str:
	for row in items:
		if not row.item_code:
			continue
		shipping_group = frappe.db.get_value(
			"Proxy SKU",
			{"item": row.item_code, "uom": row.stock_uom, "is_active": 1},
			"merchant_shipping_group",
		)
		if shipping_group:
			return shipping_group
	return ""
