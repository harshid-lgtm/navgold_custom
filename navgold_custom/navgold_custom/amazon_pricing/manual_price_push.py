# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

"""
Manual prod hotfix: push fixed min/max/our_price for one or more SKUs to Amazon.

Reuses the SP-API credentials from "Amazon SP API Settings" and submits a single
JSON_LISTINGS_FEED with one purchasable_offer PATCH per SKU.

Single / inline list:
    bench --site navgold.localhost execute \
        navgold_custom.navgold_custom.amazon_pricing.manual_price_push.run

Many SKUs from a CSV (columns: sku, min, max, our_price):
    bench --site navgold.localhost execute \
        navgold_custom.navgold_custom.amazon_pricing.manual_price_push.run \
        --kwargs "{'csv_path': '/path/to/prices.csv'}"
"""

import frappe
from frappe.utils import now_datetime
from frappe.utils.csvutils import read_csv_content
from frappe.utils.data import cstr, flt, orjson_dumps

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_submitter import (
	PRICING_FEED_TYPE,
	get_feeds_client,
)

# Default inline updates (used when no csv_path is passed).
# Each row: {"sku": <seller sku>, "min": <float>, "max": <float>, "our_price": <float>}
# NOTE: keep the default band tight. A placeholder max of 1000 against an 8.86 min is a
# ~113x ratio that the ratio guard now (correctly) rejects, so set a realistic ceiling.
DEFAULT_PRICE_UPDATES = [
	{"sku": "CLS-CS462", "min": 8.86, "max": 14.99, "our_price": 8.86},
]

# Absolute fat-finger ceiling: no manual SKU price is expected anywhere near this.
# A typo like 886.0 for 8.86, or a stray wrong max, gets blocked before it reaches Amazon.
SANE_MAX_PRICE = 5000.0

# Reject suspiciously wide bands (e.g. min=8.86 with a default max=1000 is a 112x band).
# A legitimate min/max spread is small; a huge ratio signals a placeholder or typo.
MAX_PRICE_RATIO = 100.0

# Amazon feed values are rounded to 2 dp; validate against the same rounded numbers
# so a sub-cent min (e.g. 0.004) that rounds to 0.0 in the feed is caught here.
PRICE_PRECISION = 2


def load_updates_from_csv(csv_path: str) -> list:
	with open(csv_path) as csv_file:
		rows = read_csv_content(csv_file.read())

	header = [cstr(column).strip() for column in rows[0]]
	updates = []
	for row in rows[1:]:
		record = dict(zip(header, row, strict=False))
		updates.append(
			{
				"sku": cstr(record["sku"]).strip(),
				"min": flt(record["min"]),
				"max": flt(record["max"]),
				"our_price": flt(record["our_price"]),
			}
		)
	return updates


def load_updates_from_xlsx(xlsx_path: str) -> list:
	# Expected columns: SKU, Minimum, Maximum. our_price is set to the Minimum
	# (the file carries no explicit selling price).
	import openpyxl

	workbook = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
	rows = list(workbook.active.iter_rows(values_only=True))

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


def load_updates(csv_path: str | None, xlsx_path: str | None) -> list:
	if xlsx_path:
		return load_updates_from_xlsx(xlsx_path)
	if csv_path:
		return load_updates_from_csv(csv_path)
	return DEFAULT_PRICE_UPDATES


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

		# Validate against the same rounded values the feed actually carries, so a
		# sub-cent min (0.004 -> 0.0) is caught here rather than slipping through.
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


def run(
	csv_path: str | None = None,
	xlsx_path: str | None = None,
	live: bool = False,
	preview_path: str | None = None,
) -> dict:
	# `live` is the SOLE switch. This tool was used during the June 10 incident and must
	# never submit without an explicit live=True after a reviewed dry run. We derive
	# dry_run from live unconditionally so no stray kwarg (e.g. a leftover dry_run=False)
	# can ever bypass the gate.
	dry_run = not live

	updates = load_updates(csv_path, xlsx_path)
	if not updates:
		frappe.throw("No price updates to submit")

	validate_updates(updates)

	settings = frappe.get_single("Amazon SP API Settings")
	skus = [update["sku"] for update in updates]
	json_content = build_feed(updates, settings.merchant_id, settings.get_marketplace_id())
	feed_text = json_content.decode() if isinstance(json_content, bytes) else json_content

	print(f"Prepared feed for {len(updates)} SKU(s).")
	print("First 3 rows:")
	for update in updates[:3]:
		print(f"  {update['sku']}: min={update['min']} max={update['max']} our_price={update['our_price']}")
	print("Last row:")
	last = updates[-1]
	print(f"  {last['sku']}: min={last['min']} max={last['max']} our_price={last['our_price']}")

	if not preview_path:
		preview_path = f"/tmp/manual_price_feed_{len(updates)}_skus.json"
	with open(preview_path, "w") as preview_file:
		preview_file.write(feed_text)
	print(f"\nFull feed written to: {preview_path}")

	if dry_run:
		print("Dry run — nothing submitted.")
		return {
			"dry_run": True,
			"sku_count": len(updates),
			"preview_path": preview_path,
		}

	log = frappe.new_doc("Amazon Pricing Submission Log")
	log.submission_type = "Amazon"
	log.status = "Pending"
	log.pricing_records = "\n".join(skus)
	log.feed_content = json_content
	log.insert()

	feeds_client = get_feeds_client(settings)

	doc_result = feeds_client.create_feed_document()
	feed_document_id = doc_result.get("feedDocumentId")
	upload_url = doc_result.get("url")
	log.feed_document_id = feed_document_id
	log.save()

	feeds_client.upload_feed_content(upload_url, json_content)

	create_result = feeds_client.create_feed(feed_type=PRICING_FEED_TYPE, feed_document_id=feed_document_id)
	feed_id = create_result.get("feedId")
	log.feed_id = feed_id
	log.status = "Submitted"
	log.submitted_at = now_datetime()
	log.save()

	frappe.db.commit()

	print(f"\nSubmitted feed: feed_id={feed_id}, log={log.name}")
	print(f"Check status with: check_feed_status('{log.name}')")
	return {"feed_id": feed_id, "log_name": log.name, "sku_count": len(updates), "skus": skus}
