# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import now_datetime

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
	build_pricing_feed,
	format_supplier_swap_notes,
	get_allowed_pricing_proxy_skus,
	validate_pricing_feed,
)
from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import Feeds
from navgold_custom.navgold_custom.utils import bulk_set_value

PRICING_FEED_TYPE = "JSON_LISTINGS_FEED"


def get_feeds_client(settings=None):
	if not settings:
		settings = frappe.get_single("Amazon SP API Settings")

	return Feeds(
		client_id=settings.client_id,
		client_secret=settings.get_password("client_secret"),
		refresh_token=settings.get_password("refresh_token"),
		country_code=settings.country,
	)


def submit_pricing(pricing_names: list, test_mode: bool = False) -> dict:
	settings = frappe.get_single("Amazon SP API Settings")

	seller_id = settings.merchant_id

	feed_result = build_pricing_feed(pricing_names, seller_id, settings.get_marketplace_id())
	json_content = feed_result["feed_content"]
	swap_records = feed_result["swap_records"]

	validation = validate_pricing_feed(json_content)
	if not validation["valid"]:
		return {"success": False, "error": "Feed validation failed", "errors": validation["errors"]}

	if test_mode:
		return {
			"success": True,
			"test_mode": True,
			"message_count": validation["message_count"],
			"json_preview": json_content[:2000],
			"supplier_swaps": swap_records,
		}

	log = frappe.new_doc("Amazon Pricing Submission Log")
	log.submission_type = "Amazon"
	log.status = "Pending"
	log.pricing_records = "\n".join(pricing_names)
	log.feed_content = json_content
	if swap_records:
		log.supplier_swap_notes = format_supplier_swap_notes(swap_records)
	log.insert()
	return
	try:
		feeds_client = get_feeds_client(settings)

		doc_result = feeds_client.create_feed_document()
		feed_document_id = doc_result.get("feedDocumentId")
		upload_url = doc_result.get("url")

		log.feed_document_id = feed_document_id
		log.save()

		feeds_client.upload_feed_content(upload_url, json_content)

		feed_result = feeds_client.create_feed(feed_type=PRICING_FEED_TYPE, feed_document_id=feed_document_id)
		feed_id = feed_result.get("feedId")

		log.feed_id = feed_id
		log.status = "Submitted"
		log.submitted_at = now_datetime()
		log.save()

		bulk_set_value("Amazon Product Pricing", pricing_names, "amazon_sync_status", "Submitted")
		bulk_set_value("Amazon Product Pricing", pricing_names, "last_amazon_sync", now_datetime())

		return {
			"success": True,
			"feed_id": feed_id,
			"feed_document_id": feed_document_id,
			"message_count": validation["message_count"],
			"log_name": log.name,
		}

	except Exception as e:
		log.status = "Error"
		log.error_message = str(e)
		log.save()

		bulk_set_value("Amazon Product Pricing", pricing_names, "amazon_sync_status", "Error")

		frappe.log_error(f"Amazon feed submission error: {e!s}", "Amazon Pricing Submission")

		return {"success": False, "error": str(e), "log_name": log.name}


def check_feed_status(log_name: str) -> dict:
	log = frappe.get_cached_doc("Amazon Pricing Submission Log", log_name)

	if not log.feed_id:
		frappe.throw(_("No feed ID found for log {0}").format(log_name))

	feeds_client = get_feeds_client()

	try:
		feed_status = feeds_client.get_feed(log.feed_id)

		processing_status = feed_status.get("processingStatus")
		log.amazon_status = processing_status
		log.save()

		pricing_names = [p.strip() for p in log.pricing_records.split("\n") if p.strip()]

		if processing_status == "DONE":
			log.status = "Completed"
			log.completed_at = now_datetime()

			result_doc_id = feed_status.get("resultFeedDocumentId")
			if result_doc_id:
				log.result_document_id = result_doc_id

			log.save()

			if pricing_names:
				frappe.db.set_value(
					"Amazon Product Pricing",
					{"name": ["in", pricing_names]},
					"amazon_sync_status",
					"Submitted",
				)

		elif processing_status in ["CANCELLED", "FATAL"]:
			log.status = "Error"
			log.error_message = f"Feed processing failed: {processing_status}"
			log.save()

			if pricing_names:
				frappe.db.set_value(
					"Amazon Product Pricing",
					{"name": ["in", pricing_names]},
					"amazon_sync_status",
					"Error",
				)

		return {"status": processing_status, "feed_status": feed_status}

	except Exception as e:
		frappe.log_error(f"Feed status check error for {log_name}: {e!s}", "Amazon Pricing")
		frappe.throw(_("Failed to check feed status: {0}").format(str(e)))


@frappe.whitelist()
def bulk_submit_pricing(pricing_names: str, test_mode: bool = False) -> dict:
	if isinstance(pricing_names, str):
		try:
			pricing_names = frappe.parse_json(pricing_names)
		except Exception:
			pricing_names = [p.strip() for p in pricing_names.split(",") if p.strip()]

	if not pricing_names:
		frappe.throw(_("No pricing records provided"))

	return submit_pricing(pricing_names, test_mode=test_mode)


def submit_active_pricing() -> dict:
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
		return {"success": True, "message": "No eligible pricing records to submit", "submitted_count": 0}

	result = submit_pricing(pricing_names) or {}

	frappe.db.set_value(
		"Navgold Settings",
		"Navgold Settings",
		"last_pricing_synced_at",
		now_datetime(),
		update_modified=False,
	)

	result["submitted_count"] = len(pricing_names)
	return result
