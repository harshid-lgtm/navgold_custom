# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe.utils.csvutils import read_csv_content
from frappe.utils.data import cint, cstr, flt, orjson_dumps
from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_submitter import (
	PRICING_FEED_TYPE,
	get_feeds_client,
)
from navgold_custom.navgold_custom.amazon_pricing.submission_log import record_submission_error
from navgold_custom.navgold_custom.utils import AmazonFrozenError, PricingNotApprovedError

# Absolute fat-finger ceiling: a typo like 886.0 for 8.86 gets blocked before it reaches Amazon.
SANE_MAX_PRICE = 5000.0

# A huge min/max ratio signals a placeholder or typo, not a legitimate band.
MAX_PRICE_RATIO = 100.0

# Amazon feed values are rounded to 2 dp; validate against the same rounded numbers
# so a sub-cent min (e.g. 0.004) that rounds to 0.0 in the feed is caught here.
PRICE_PRECISION = 2


def parse_csv_rows(rows: list) -> list:
	header = [cstr(column).strip() for column in rows[0]]
	updates = []
	for row in rows[1:]:
		record = dict(zip(header, row, strict=False))
		sku = cstr(record.get("sku")).strip()
		if not sku:
			continue
		updates.append(
			{
				"sku": sku,
				"min": flt(record.get("min")),
				"max": flt(record.get("max")),
				"our_price": flt(record.get("our_price")),
			}
		)
	return updates


def parse_xlsx_rows(rows: list) -> list:
	# Columns: SKU, Minimum, Maximum. our_price is set to the Minimum
	# (the file carries no explicit selling price).
	header = [cstr(column).strip().lower() for column in rows[0]]
	sku_index = header.index("sku")
	min_index = header.index("minimum")
	max_index = header.index("maximum")

	updates = []
	for row in rows[1:]:
		sku = cstr(row[sku_index]).strip()
		if not sku:
			continue
		minimum = flt(row[min_index])
		updates.append(
			{
				"sku": sku,
				"min": minimum,
				"max": flt(row[max_index]),
				"our_price": minimum,
			}
		)
	return updates


def load_updates_from_attachment(file_url: str) -> list:
	content = frappe.get_doc("File", {"file_url": file_url}).get_content()
	if cstr(file_url).lower().endswith(".xlsx"):
		return parse_xlsx_rows(read_xlsx_file_from_attached_file(fcontent=content))
	if isinstance(content, bytes):
		content = content.decode("utf-8")
	return parse_csv_rows(read_csv_content(content))


def build_purchasable_offer(update: dict) -> dict:
	return {
		"audience": "ALL",
		"our_price": [{"schedule": [{"value_with_tax": round(flt(update["our_price"]), 2)}]}],
		"minimum_seller_allowed_price": [{"schedule": [{"value_with_tax": round(flt(update["min"]), 2)}]}],
		"maximum_seller_allowed_price": [{"schedule": [{"value_with_tax": round(flt(update["max"]), 2)}]}],
	}


def build_feed(updates: list, seller_id: str, marketplace_id: str) -> bytes:
	messages = []
	for index, update in enumerate(updates, start=1):
		messages.append(
			{
				"messageId": index,
				"sku": update["sku"],
				"operationType": "PATCH",
				"productType": "PRODUCT",
				"patches": [
					{
						"op": "replace",
						"path": "/attributes/purchasable_offer",
						"value": [build_purchasable_offer(update)],
					}
				],
			}
		)
	feed = {
		"header": {"sellerId": seller_id, "version": "2.0", "issueLocale": "en_GB"},
		"marketplaceIds": [marketplace_id],
		"messages": messages,
	}
	return orjson_dumps(feed)


def fetch_feed_result(log_name: str) -> dict:
	import gzip

	from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import download_binary

	log = frappe.get_doc("Amazon Pricing Submission Log", log_name)
	feeds_client = get_feeds_client()

	feed_status = feeds_client.get_feed(log.feed_id)
	processing_status = feed_status.get("processingStatus")
	result_document_id = feed_status.get("resultFeedDocumentId")

	print(f"{log_name}: processingStatus={processing_status}")

	if not result_document_id:
		print("No result document yet (feed not finished or produced no report).")
		return {"processing_status": processing_status, "result": None}

	document = feeds_client.get_feed_document(result_document_id)
	content = download_binary(document["url"])
	if (document.get("compressionAlgorithm") or "").upper() == "GZIP":
		content = gzip.decompress(content)
	try:
		report = content.decode("utf-8")
	except UnicodeDecodeError:
		report = content.decode("cp1252")

	print("\n--- Feed result document ---")
	print(report)
	return {"processing_status": processing_status, "result": frappe.parse_json(report)}


def validate_updates(updates: list) -> None:
	problems = []
	for update in updates:
		sku = cstr(update.get("sku")).strip() or "<missing sku>"

		# Distinguish a missing/blank CSV column from a genuine zero value so a
		# malformed file is debuggable instead of reporting a misleading "min=0.0".
		missing = [field for field in ("min", "max", "our_price") if update.get(field) in (None, "")]
		if missing:
			problems.append(f"{sku}: missing field(s): {', '.join(missing)}")
			continue

		# Validate against the rounded values the feed actually carries (see PRICE_PRECISION).
		minimum = flt(update.get("min"), precision=PRICE_PRECISION)
		maximum = flt(update.get("max"), precision=PRICE_PRECISION)
		our_price = flt(update.get("our_price"), precision=PRICE_PRECISION)

		if minimum <= 0 or maximum <= 0 or our_price <= 0:
			problems.append(f"{sku}: non-positive price (min={minimum} max={maximum} our={our_price})")
		elif minimum > maximum:
			problems.append(f"{sku}: min {minimum} above max {maximum}")
		elif not (minimum <= our_price <= maximum):
			problems.append(f"{sku}: our_price {our_price} outside [{minimum}, {maximum}]")
		elif maximum > SANE_MAX_PRICE or our_price > SANE_MAX_PRICE:
			problems.append(
				f"{sku}: price above sane ceiling {SANE_MAX_PRICE} (max={maximum} our={our_price})"
			)
		elif maximum / minimum > MAX_PRICE_RATIO:
			problems.append(
				f"{sku}: max/min ratio {flt(maximum / minimum, precision=PRICE_PRECISION)} "
				f"exceeds {MAX_PRICE_RATIO} (min={minimum} max={maximum})"
			)

	if problems:
		message = "Manual price push blocked:\n" + "\n".join(problems[:50])
		if len(problems) > 50:
			message += f"\n… and {len(problems) - 50} more"
		frappe.throw(message)


def sync_pricing_band(updates: list) -> int:
	# A manual push writes a band straight to Amazon, bypassing the strategy calc that normally
	# keeps current_min/max in step. Mirror the pushed band back so the daily snapshot stays faithful.
	by_sku = {update["sku"]: update for update in updates}
	existing = frappe.get_all(
		"Amazon Product Pricing",
		filters={"name": ["in", list(by_sku)]},
		pluck="name",
	)
	band_updates = {
		name: {
			"current_min_price": flt(by_sku[name]["min"]),
			"current_max_price": flt(by_sku[name]["max"]),
		}
		for name in existing
	}
	if band_updates:
		frappe.db.bulk_update("Amazon Product Pricing", band_updates)
	return len(band_updates)


def submit_feed(updates: list, settings) -> dict:
	skus = [update["sku"] for update in updates]
	json_content = build_feed(updates, settings.merchant_id, settings.get_marketplace_id())

	log = frappe.new_doc("Amazon Pricing Submission Log")
	log.submission_type = "Amazon"
	log.status = "Pending"
	log.pricing_records = "\n".join(skus)
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

		create_result = feeds_client.create_feed(
			feed_type=PRICING_FEED_TYPE, feed_document_id=feed_document_id
		)
		feed_id = create_result.get("feedId")
		log.feed_id = feed_id
		log.status = "Submitted"
		log.submitted_at = now_datetime()
		log.save()

		synced = sync_pricing_band(updates)
	except (AmazonFrozenError, PricingNotApprovedError) as e:
		# Intentional gate, not a failure — keep it out of the error queue.
		log.status = "Skipped"
		log.error_message = str(e)
		log.save()
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
		raise
	except Exception as e:
		record_submission_error(log, channel="Amazon", exception=e)
		raise

	# Persist the submission log + band before returning to the caller.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit

	return {
		"feed_id": feed_id,
		"log_name": log.name,
		"sku_count": len(updates),
		"skus": skus,
		"band_synced": synced,
	}


@frappe.whitelist()
def push_manual_prices(updates: str | list, live: bool = False) -> dict:
	# Defaults to a dry run so a reviewed preview always precedes a real push.
	frappe.only_for("System Manager")

	# `live` arrives from the web as a string ("true"/"false"); bool("false") is truthy, so
	# coerce explicitly — this is the switch that decides whether real prices reach Amazon.
	live = cint(live)

	updates = frappe.parse_json(updates) if isinstance(updates, str) else updates
	normalised = [
		{
			"sku": cstr(update.get("sku")).strip(),
			"min": flt(update.get("min")),
			"max": flt(update.get("max")),
			"our_price": flt(update.get("our_price")),
		}
		for update in updates
		if cstr(update.get("sku")).strip()
	]
	if not normalised:
		frappe.throw(_("No SKUs supplied for manual price push."))

	validate_updates(normalised)

	if not live:
		return {
			"dry_run": True,
			"sku_count": len(normalised),
			"updates": normalised,
		}

	settings = frappe.get_single("Amazon SP API Settings")
	return submit_feed(normalised, settings)
