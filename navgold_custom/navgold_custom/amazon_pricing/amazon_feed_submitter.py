# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe.utils.data import convert_utc_to_system_timezone, cstr, get_datetime
from frappe.utils.synchronization import filelock

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
	build_pricing_feed,
	format_supplier_fallback_notes,
	get_allowed_pricing_proxy_skus,
	validate_pricing_feed,
)
from navgold_custom.navgold_custom.amazon_pricing.price_guard import notify_price_deviation_blocks
from navgold_custom.navgold_custom.amazon_pricing.pricing_validators import (
	format_blocked_records,
	merge_blocked_reasons,
)
from navgold_custom.navgold_custom.amazon_pricing.submission_log import record_submission_error
from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import Feeds, SPAPIError
from navgold_custom.navgold_custom.utils import (
	AmazonFrozenError,
	PricingNotApprovedError,
	bulk_set_value,
)

PRICING_FEED_TYPE = "JSON_LISTINGS_FEED"

# Serialises the Amazon Pricing Submission Log insert across the concurrent pricing +
# repricer jobs (both fire on the same cron). Without this the naming-series row lock is
# held across the slow SP-API calls, so the second job hits a 50s "Lock wait timeout".
SUBMISSION_LOG_LOCK = "navgold_pricing_submission_log"


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
	fallback_repricings = feed_result["fallback_repricings"]
	blocked_price_deviation = feed_result.get("blocked_price_deviation") or []
	blocked_validation = feed_result.get("blocked_validation") or []
	blocked_reasons = merge_blocked_reasons(blocked_validation, blocked_price_deviation)

	validation = validate_pricing_feed(
		json_content, require_pricing_patches=feed_result.get("publishing_mode") != "Stock Only"
	)
	if not validation["valid"]:
		return {
			"success": False,
			"error": "Feed validation failed",
			"errors": validation["errors"],
			"blocked_price_deviation": blocked_price_deviation,
			"blocked_validation": blocked_validation,
		}

	if test_mode:
		return {
			"success": True,
			"test_mode": True,
			"message_count": validation["message_count"],
			"json_preview": json_content[:2000],
			"fallback_repricings": fallback_repricings,
			"blocked_price_deviation": blocked_price_deviation,
			"blocked_validation": blocked_validation,
		}

	log = frappe.new_doc("Amazon Pricing Submission Log")
	log.submission_type = "Amazon"
	log.status = "Pending"
	log.pricing_records = "\n".join(pricing_names)
	log.feed_content = json_content
	if fallback_repricings:
		log.supplier_swap_notes = format_supplier_fallback_notes(fallback_repricings)
	if blocked_reasons:
		log.blocked_records = format_blocked_records(blocked_reasons)
		log.blocked_count = len(blocked_reasons)
	with filelock(SUBMISSION_LOG_LOCK, timeout=120):
		log.insert()
		frappe.db.commit()  # nosemgrep: frappe-manual-commit -- free the APSL series lock before slow SP-API calls
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
			"blocked_price_deviation": blocked_price_deviation,
			"blocked_validation": blocked_validation,
		}

	except (AmazonFrozenError, PricingNotApprovedError) as e:
		# Intentional gate (kill switch or Dry Run), not a transport failure. Mark the records
		# "Skipped" so they stay out of the failed-sync triage queue and don't read as real errors.
		log.status = "Skipped"
		log.error_message = str(e)
		log.save()

		bulk_set_value("Amazon Product Pricing", pricing_names, "amazon_sync_status", "Skipped")

		return {
			"success": False,
			"skipped": True,
			"error": str(e),
			"log_name": log.name,
			"blocked_price_deviation": blocked_price_deviation,
			"blocked_validation": blocked_validation,
		}

	except Exception as e:
		bulk_set_value("Amazon Product Pricing", pricing_names, "amazon_sync_status", "Error")

		record_submission_error(log, channel="Amazon", exception=e)

		return {
			"success": False,
			"error": str(e),
			"log_name": log.name,
			"blocked_price_deviation": blocked_price_deviation,
			"blocked_validation": blocked_validation,
		}


def is_feed_not_found_error(error: Exception) -> bool:
	if not isinstance(error, SPAPIError):
		return False

	description = cstr(error.error_description).lower().replace(" ", "")
	return "404" in cstr(error.error) or "notfound" in description


def get_feed_completed_at(feed_status: dict):
	# Client ask: completed_at should reflect when Amazon finished processing, not when the
	# status check happened. Amazon returns UTC ISO 8601; store naive system-local like every
	# other Datetime field.
	processing_end_time = get_datetime(feed_status.get("processingEndTime"))
	if not processing_end_time:
		return now_datetime()

	if processing_end_time.tzinfo:
		processing_end_time = convert_utc_to_system_timezone(processing_end_time).replace(tzinfo=None)

	return processing_end_time


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
			log.completed_at = get_feed_completed_at(feed_status)

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
			if pricing_names:
				frappe.db.set_value(
					"Amazon Product Pricing",
					{"name": ["in", pricing_names]},
					"amazon_sync_status",
					"Error",
				)

			record_submission_error(
				log, message=f"Feed processing failed: {processing_status}", channel="Amazon"
			)

		return {"status": processing_status, "feed_status": feed_status}

	except Exception as e:
		if is_feed_not_found_error(e):
			# Amazon eventually drops old feed ids; leaving the log "Submitted" would make the
			# scheduled poller retry a feed that can never resolve.
			record_submission_error(
				log,
				message=f"Feed {log.feed_id} not found on Amazon: {e.error_description}",
				channel="Amazon",
				alert=False,
			)
			return {"status": "NOT_FOUND", "feed_status": None}

		frappe.log_error(
			f"Feed status check error for {log_name}:\n{frappe.get_traceback(with_context=True)}",
			"Amazon Pricing",
		)
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

	notify_price_deviation_blocks(result.get("blocked_price_deviation") or [], "Amazon")

	frappe.db.set_single_value(
		"Navgold Settings",
		"last_pricing_synced_at",
		now_datetime(),
		update_modified=False,
	)

	result["submitted_count"] = len(pricing_names)
	return result
