# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from urllib.parse import urlparse
from xml.dom import minidom
from xml.sax.saxutils import escape

import frappe
import requests
from frappe import _
from frappe.integrations.utils import create_request_log
from frappe.utils import cint, flt
from frappe.utils.background_jobs import enqueue, is_job_enqueued
from frappe.utils.csvutils import to_csv
from frappe.utils.file_manager import save_file
from frappe.utils.synchronization import LockTimeoutError, filelock

from navgold_custom.navgold_custom.doctype.supplier_item_price_and_stock.supplier_item_price_and_stock import (
	update_supplier_rank,
)
from navgold_custom.navgold_custom.doctype.supplier_stock_import.supplier_stock_import import (
	TEMPLATE_COLUMNS,
)

TIMEOUT = 60
CLF_STOCK_BATCH_SIZE = 500
CLF_PRICE_BATCH_SIZE = 250
CLF_SUPPLIER = "CLF Distribution"
CLF_STOCK_FETCH_JOB_ID = "clf_stock_fetch"
CLF_PRICE_FETCH_JOB_ID = "clf_price_fetch"
CLF_SCHEDULED_FETCH_JOB_ID = "clf_scheduled_fetch"
CLF_FETCH_LOCK = "clf_fetch"


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


def parse_products(xml_text, result_tag, field, cast):
	inner = extract_text(xml_text, result_tag)
	values = {}
	if not inner or not inner.strip():
		return values
	dom = minidom.parseString(inner)
	for product in dom.getElementsByTagName("Product"):
		sku = _child_text(product, "sku")
		value = _child_text(product, field)
		if sku and value is not None:
			values[sku] = cast(value)
	return values


def _child_text(node, tag):
	for child in node.getElementsByTagName(tag):
		if child.firstChild:
			return child.firstChild.nodeValue
	return None


# CLF omits SKUs it does not recognise from the response, so the map only holds SKUs it
# returned; the caller decides how to treat a missing one (stock -> out of stock, price -> keep).
def get_product_field(skus, operation, result_tag, field, cast, fault_label, batch_size):
	unique_skus = list(dict.fromkeys(sku for sku in skus if sku))
	token = get_token()
	values = {}
	for start in range(0, len(unique_skus), batch_size):
		batch = unique_skus[start : start + batch_size]
		inner = (
			"<ProductCodes>"
			+ "".join(f"<Code><sku>{escape(str(s))}</sku></Code>" for s in batch)
			+ "</ProductCodes>"
		)
		xml_text = call(operation, params=[("productCodesXml", inner)], token=token)
		fault = extract_text(xml_text, "faultstring") or extract_text(xml_text, "ErrorMessage")
		if fault and fault.strip():
			frappe.throw(_("CLF {0} fetch failed: {1}").format(fault_label, fault.strip()))
		values.update(parse_products(xml_text, result_tag, field, cast))
	return values


def get_product_stock(skus):
	return get_product_field(
		skus, "GetProductStock", "GetProductStockResult", "stock", cint, _("stock"), CLF_STOCK_BATCH_SIZE
	)


def get_product_prices(skus):
	prices = get_product_field(
		skus, "GetProductData", "GetProductDataResult", "price", flt, _("price"), CLF_PRICE_BATCH_SIZE
	)
	return {sku: price for sku, price in prices.items() if price > 0}


@frappe.whitelist()
def fetch_clf_stock():
	enqueue_clf_fetch("stock", CLF_STOCK_FETCH_JOB_ID)


@frappe.whitelist()
def fetch_clf_price():
	enqueue_clf_fetch("price", CLF_PRICE_FETCH_JOB_ID)


def schedule_clf_fetch():
	settings = frappe.get_cached_doc("Navgold Settings")
	if not (settings.clf_endpoint and settings.clf_username and settings.clf_password):
		return

	enqueue(
		run_scheduled_clf_fetch,
		queue="long",
		job_id=CLF_SCHEDULED_FETCH_JOB_ID,
		deduplicate=True,
		timeout=3600,
	)


def run_scheduled_clf_fetch():
	for metric in ("stock", "price"):
		run_clf_fetch("Administrator", metric)


def enqueue_clf_fetch(metric, job_id):
	if is_job_enqueued(job_id):
		frappe.throw(_("A CLF {0} fetch is already in progress").format(metric))

	enqueue(
		run_clf_fetch,
		queue="long",
		job_id=job_id,
		user=frappe.session.user,
		metric=metric,
	)


def run_clf_fetch(user, metric="stock"):
	try:
		with filelock(CLF_FETCH_LOCK, timeout=0):
			summary = fetch_and_import_clf(metric)
	except LockTimeoutError:
		report_clf_fetch_failure(
			metric, _("Another CLF fetch is already running. Please wait for it to finish."), user
		)
	except frappe.ValidationError as validation_error:
		report_clf_fetch_failure(metric, str(validation_error), user)
	except requests.exceptions.RequestException as request_error:
		report_clf_fetch_failure(
			metric,
			_("CLF endpoint is unreachable. It may be down — try again later."),
			user,
			error=str(request_error),
		)
	except Exception:
		report_clf_fetch_failure(
			metric, _("CLF {0} fetch failed unexpectedly.").format(metric), user, error=frappe.get_traceback()
		)
	else:
		create_clf_request_log(output=summary, reference_docname=summary["docname"])
		frappe.publish_realtime("clf_fetched", {"metric": metric, "docname": summary["docname"]}, user=user)


def report_clf_fetch_failure(metric, message, user, error=None):
	create_clf_request_log(error=error or message)
	frappe.publish_realtime("clf_fetched", {"metric": metric, "error": message}, user=user)


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


def fetch_and_import_clf(metric):
	records = frappe.get_all(
		"Supplier Item Price and Stock",
		filters={"supplier": CLF_SUPPLIER, "supplier_part_number": ["is", "set"]},
		fields=["supplier_part_number", "uom", "barcode"],
	)
	if not records:
		frappe.throw(_("No {0} records with a Supplier Part Number to fetch").format(CLF_SUPPLIER))

	skus = [record.supplier_part_number for record in records]
	value_map = get_product_prices(skus) if metric == "price" else get_product_stock(skus)

	csv_rows = []
	for record in records:
		in_clf = record.supplier_part_number in value_map

		if metric == "price":
			# Only update prices CLF returned; a missing or unpriced SKU keeps its current price.
			if not in_clf:
				continue
			stock_value, price_value = "", value_map[record.supplier_part_number]
		else:
			# A SKU CLF no longer lists is out of stock, so default missing ones to 0.
			stock_value, price_value = value_map.get(record.supplier_part_number, 0), ""

		csv_rows.append(
			[
				record.supplier_part_number,
				stock_value,
				record.uom,
				price_value,
				record.barcode or "",
			]
		)

	if not csv_rows:
		frappe.throw(_("CLF returned no {0} for any {1} part number").format(metric, CLF_SUPPLIER))

	doc = frappe.get_doc(
		{
			"doctype": "Supplier Stock Import",
			"supplier": CLF_SUPPLIER,
			"currency": frappe.db.get_value("Supplier", CLF_SUPPLIER, "default_currency"),
			"use_supplier_part_number": 1,
			"import_stock": 1 if metric == "stock" else 0,
			"import_price": 1 if metric == "price" else 0,
		}
	).insert(ignore_permissions=True)

	file_doc = save_file(
		f"CLF {metric.title()} {doc.name}.csv",
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
		"skus_returned": len(value_map),
		"rows_imported": len(csv_rows),
	}


def build_clf_csv(rows):
	return to_csv([TEMPLATE_COLUMNS, *rows])
