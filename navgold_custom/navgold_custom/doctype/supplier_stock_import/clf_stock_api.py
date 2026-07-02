# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from urllib.parse import urlparse
from xml.dom import minidom
from xml.sax.saxutils import escape

import frappe
import requests
from frappe import _

TIMEOUT = 60
STOCK_BATCH_SIZE = 500


def get_endpoint():
	endpoint = frappe.db.get_single_value("Navgold Settings", "clf_endpoint")
	if not endpoint:
		frappe.throw(_("Set the CLF Stock Import Endpoint in Navgold Settings → Stock tab."))
	return endpoint


# The SOAP namespace is the endpoint without its port and without the .asmx
# filename, e.g. .../CLFWebOrdering/WebOrdering.asmx → .../CLFWebOrdering.
def get_namespace(endpoint):
	parsed = urlparse(endpoint)
	path = parsed.path.rsplit("/", 1)[0]
	return f"{parsed.scheme}://{parsed.hostname}{path}"


def build_envelope(operation, body_inner, namespace, token=None):
	# WebServiceHeader must ALWAYS be present, even for GetAuthenticationToken —
	# omitting it makes the server throw a NullReferenceException.
	token_node = f"<AuthenticationToken>{escape(token)}</AuthenticationToken>" if token else ""
	header = (
		"<soap:Header>"
		f'<WebServiceHeader xmlns="{namespace}">{token_node}</WebServiceHeader>'
		"</soap:Header>"
	)
	return (
		'<?xml version="1.0" encoding="utf-8"?>'
		'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
		f"{header}"
		"<soap:Body>"
		f'<{operation} xmlns="{namespace}">{body_inner}</{operation}>'
		"</soap:Body>"
		"</soap:Envelope>"
	)


def call(operation, params=None, token=None):
	endpoint = get_endpoint()
	namespace = get_namespace(endpoint)
	body_inner = ""
	for name, value in params or []:
		body_inner += f"<{name}>{escape(str(value))}</{name}>"
	envelope = build_envelope(operation, body_inner, namespace, token=token)
	headers = {
		"Content-Type": "text/xml; charset=utf-8",
		"SOAPAction": f'"{namespace}/{operation}"',
	}
	response = requests.post(endpoint, data=envelope.encode("utf-8"), headers=headers, timeout=TIMEOUT)
	# SOAP faults come back as HTTP 500 with a fault body — keep the body, don't raise.
	if response.status_code >= 400 and "Fault" not in response.text:
		response.raise_for_status()
	return response.text


def extract_text(xml_text, tag):
	try:
		dom = minidom.parseString(xml_text)
	except Exception:
		return None
	for node in dom.getElementsByTagName("*"):
		if node.localName == tag and node.firstChild:
			return node.firstChild.nodeValue
	return None


def get_credentials():
	settings = frappe.get_cached_doc("Navgold Settings")
	username = settings.clf_username
	password = settings.get_password("clf_password", raise_exception=False) if settings.clf_password else None
	if not username or not password:
		frappe.throw(_("Set the CLF Username and Password in Navgold Settings → Stock tab."))
	return username, password


def get_token():
	username, password = get_credentials()
	xml_text = call(
		"GetAuthenticationToken",
		params=[("Username", username), ("Password", password)],
	)
	token = extract_text(xml_text, "GetAuthenticationTokenResult")
	if not token:
		reason = extract_text(xml_text, "faultstring") or extract_text(xml_text, "ErrorMessage")
		frappe.throw(_("CLF authentication failed: {0}").format(reason or _("no token returned")))
	return token


def parse_stock(xml_text):
	inner = extract_text(xml_text, "GetProductStockResult")
	stock = {}
	if not inner or not inner.strip():
		return stock
	dom = minidom.parseString(inner)
	for product in dom.getElementsByTagName("Product"):
		sku = _child_text(product, "sku")
		if sku:
			stock[sku] = frappe.cint(_child_text(product, "stock"))
	return stock


def _child_text(node, tag):
	for child in node.getElementsByTagName(tag):
		if child.firstChild:
			return child.firstChild.nodeValue
	return None


# The CLF catalogue is sparse: SKUs it does not recognise are simply omitted from
# the response, so callers must treat a missing SKU as "unknown", not zero stock.
def get_product_stock(skus):
	unique_skus = list(dict.fromkeys(sku for sku in skus if sku))
	token = get_token()
	stock = {}
	for start in range(0, len(unique_skus), STOCK_BATCH_SIZE):
		batch = unique_skus[start : start + STOCK_BATCH_SIZE]
		inner = (
			"<ProductCodes>"
			+ "".join(f"<Code><sku>{escape(str(s))}</sku></Code>" for s in batch)
			+ "</ProductCodes>"
		)
		xml_text = call("GetProductStock", params=[("productCodesXml", inner)], token=token)
		fault = extract_text(xml_text, "faultstring") or extract_text(xml_text, "ErrorMessage")
		if fault and fault.strip():
			frappe.throw(_("CLF stock fetch failed: {0}").format(fault.strip()))
		stock.update(parse_stock(xml_text))
	return stock
