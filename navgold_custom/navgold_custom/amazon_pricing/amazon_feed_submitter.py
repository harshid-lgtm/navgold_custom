# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import now_datetime

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
	build_pricing_feed,
	validate_pricing_feed,
)
from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import Feeds

PRICING_FEED_TYPE = "JSON_LISTINGS_FEED"


def get_feeds_client(settings=None):
	if not settings:
		settings = frappe.get_last_doc("Amazon SP API Settings", {"is_active": 1})

	return Feeds(
		client_id=settings.client_id,
		client_secret=settings.get_password("client_secret"),
		refresh_token=settings.get_password("refresh_token"),
		country_code=settings.country,
	)


def submit_pricing(pricing_names: list, test_mode: bool = False) -> dict:
	settings = frappe.get_last_doc("Amazon SP API Settings", {"is_active": 1})

	seller_id = settings.merchant_id

	json_content = build_pricing_feed(pricing_names, seller_id, settings.get_marketplace_id())

	validation = validate_pricing_feed(json_content)
	if not validation["valid"]:
		return {"success": False, "error": "Feed validation failed", "errors": validation["errors"]}

	if test_mode:
		return {
			"success": True,
			"test_mode": True,
			"message_count": validation["message_count"],
			"json_preview": json_content[:2000],
		}

	log = frappe.new_doc("Amazon Pricing Submission Log")
	log.submission_type = "Amazon"
	log.status = "Pending"
	log.pricing_records = "\n".join(pricing_names)
	log.feed_content = json_content
	log.insert()

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

		for pricing_name in pricing_names:
			frappe.db.set_value(
				"Amazon Product Pricing",
				pricing_name,
				{"amazon_sync_status": "Submitted", "last_amazon_sync": now_datetime()},
			)

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

		for pricing_name in pricing_names:
			frappe.db.set_value("Amazon Product Pricing", pricing_name, "amazon_sync_status", "Error")

		frappe.log_error(f"Amazon feed submission error: {e!s}", "Amazon Pricing Submission")

		return {"success": False, "error": str(e), "log_name": log.name}


def check_feed_status(log_name: str) -> dict:
	log = frappe.get_cached_doc("Amazon Pricing Submission Log", log_name)

	if not log.feed_id:
		return {"success": False, "error": "No feed ID found"}

	feeds_client = get_feeds_client()

	try:
		feed_status = feeds_client.get_feed(log.feed_id)

		processing_status = feed_status.get("processingStatus")
		log.amazon_status = processing_status
		log.save()

		if processing_status == "DONE":
			log.status = "Completed"
			log.completed_at = now_datetime()

			result_doc_id = feed_status.get("resultFeedDocumentId")
			if result_doc_id:
				log.result_document_id = result_doc_id

			log.save()

			for pricing_name in log.pricing_records.split("\n"):
				if pricing_name.strip():
					frappe.db.set_value(
						"Amazon Product Pricing", pricing_name.strip(), "amazon_sync_status", "Submitted"
					)

		elif processing_status in ["CANCELLED", "FATAL"]:
			log.status = "Error"
			log.error_message = f"Feed processing failed: {processing_status}"
			log.save()

			for pricing_name in log.pricing_records.split("\n"):
				if pricing_name.strip():
					frappe.db.set_value(
						"Amazon Product Pricing", pricing_name.strip(), "amazon_sync_status", "Error"
					)

		return {"success": True, "status": processing_status, "feed_status": feed_status}

	except Exception as e:
		frappe.log_error(f"Feed status check error: {e!s}", "Amazon Pricing")
		return {"success": False, "error": str(e)}


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
