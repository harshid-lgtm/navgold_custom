# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from urllib.parse import urlparse
from xml.dom import minidom
from xml.sax.saxutils import escape

import frappe
import requests
from frappe import _
from frappe.integrations.utils import create_request_log
from frappe.utils.background_jobs import enqueue, is_job_enqueued
from frappe.utils.csvutils import to_csv
from frappe.utils.file_manager import save_file

from navgold_custom.navgold_custom.doctype.supplier_item_price_and_stock.supplier_item_price_and_stock import (
	update_supplier_rank,
)
from navgold_custom.navgold_custom.doctype.supplier_stock_import.supplier_stock_import import (
	TEMPLATE_COLUMNS,
)

TIMEOUT = 60
STOCK_BATCH_SIZE = 500
CLF_SUPPLIER = "CLF Distribution"
CLF_FETCH_JOB_ID = "clf_stock_fetch"


def get_endpoint():
	endpoint = frappe.db.get_single_value("Navgold Settings", "clf_endpoint")
	if not endpoint:
		frappe.throw(_("Set the CLF Stock Import Endpoint in Navgold Settings → Stock tab."))
	return endpoint


def get_namespace(endpoint):
	parsed = urlparse(endpoint)
	path = parsed.path.rsplit("/", 1)[0]
	return f"{parsed.scheme}://{parsed.hostname}{path}"


def build_envelope(operation, body_inner, namespace, token=None):
	token_node = f"<AuthenticationToken>{escape(token)}</AuthenticationToken>" if token else ""
	header = (
		f'<soap:Header><WebServiceHeader xmlns="{namespace}">{token_node}</WebServiceHeader></soap:Header>'
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


@frappe.whitelist()
def fetch_clf_stock():
	if is_job_enqueued(CLF_FETCH_JOB_ID):
		frappe.throw(_("A CLF stock fetch is already in progress"))

	enqueue(
		run_clf_fetch,
		queue="long",
		job_id=CLF_FETCH_JOB_ID,
		user=frappe.session.user,
	)


def run_clf_fetch(user):
	try:
		summary = fetch_and_import_clf_stock()
	except frappe.ValidationError as validation_error:
		report_clf_fetch_failure(str(validation_error), user)
	except requests.exceptions.RequestException as request_error:
		report_clf_fetch_failure(
			_("CLF endpoint is unreachable. It may be down — try again later."),
			user,
			error=str(request_error),
		)
	except Exception:
		report_clf_fetch_failure(
			_("CLF stock fetch failed unexpectedly."), user, error=frappe.get_traceback()
		)
	else:
		create_clf_request_log(output=summary, reference_docname=summary["docname"])
		frappe.publish_realtime("clf_stock_fetched", {"docname": summary["docname"]}, user=user)


def report_clf_fetch_failure(message, user, error=None):
	create_clf_request_log(error=error or message)
	frappe.publish_realtime("clf_stock_fetched", {"error": message}, user=user)


def create_clf_request_log(output=None, error=None, reference_docname=None):
	create_request_log(
		{"supplier": CLF_SUPPLIER},
		service_name="CLF Distribution",
		url=frappe.db.get_single_value("Navgold Settings", "clf_endpoint"),
		is_remote_request=1,
		reference_doctype="Supplier Stock Import" if reference_docname else None,
		reference_docname=reference_docname,
		output=output,
		error=error,
		status="Failed" if error else "Completed",
	)


def fetch_and_import_clf_stock():
	records = frappe.get_all(
		"Supplier Item Price and Stock",
		filters={"supplier": CLF_SUPPLIER, "supplier_part_number": ["is", "set"]},
		fields=["supplier_part_number", "uom", "barcode"],
	)
	if not records:
		frappe.throw(_("No {0} records with a Supplier Part Number to fetch").format(CLF_SUPPLIER))

	stock_map = get_product_stock([record.supplier_part_number for record in records])

	csv_rows = []
	for record in records:
		# Skip SKUs CLF did not return so the existing stock is left untouched.
		if record.supplier_part_number not in stock_map:
			continue
		csv_rows.append(
			[
				record.supplier_part_number,
				stock_map[record.supplier_part_number],
				record.uom,
				"",
				record.barcode or "",
			]
		)

	if not csv_rows:
		frappe.throw(_("CLF returned no stock for any {0} part number").format(CLF_SUPPLIER))

	doc = frappe.get_doc(
		{
			"doctype": "Supplier Stock Import",
			"supplier": CLF_SUPPLIER,
			"currency": frappe.db.get_value("Supplier", CLF_SUPPLIER, "default_currency"),
			"use_supplier_part_number": 1,
			"import_stock": 1,
			"import_price": 0,
		}
	).insert(ignore_permissions=True)

	file_doc = save_file(
		f"CLF Stock {doc.name}.csv",
		build_clf_csv(csv_rows),
		doc.doctype,
		doc.name,
		is_private=1,
	)
	doc.entry_list = file_doc.file_url
	doc.save(ignore_permissions=True)

	doc.process_import()
	doc.save(ignore_permissions=True)
	update_supplier_rank()

	return {
		"docname": doc.name,
		"records_requested": len(records),
		"skus_returned": len(stock_map),
		"rows_imported": len(csv_rows),
	}


def build_clf_csv(rows):
	return to_csv([TEMPLATE_COLUMNS, *rows])
