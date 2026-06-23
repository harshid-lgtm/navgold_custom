# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import gzip
from datetime import datetime, timedelta

import frappe
from frappe.utils import convert_utc_to_system_timezone, get_datetime, now_datetime
from frappe.utils.data import cint

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_submitter import get_feeds_client
from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import (
	SPAPIError,
	download_binary,
	retry_with_backoff,
)

PRICING_FEED_TYPE = "JSON_LISTINGS_FEED"
# Amazon retains only ~90 days of feed history; never ask for more.
MAX_LOOKBACK_DAYS = 88
# First-ever sync (empty ledger) seeds this window; later runs are incremental.
INITIAL_LOOKBACK_HOURS = 48
# Re-scan recent feeds so an IN_PROGRESS row picks up its final status and counts.
OVERLAP_HOURS = 6
# Politeness cap per run — incremental sync only ever sees a handful of new feeds.
MAX_PAGES_PER_RUN = 12
PAGE_SIZE = 100
# Result documents cost one throttled call each; cap per run and let the next run catch up.
MAX_RESULTS_PER_RUN = 60
TERMINAL_STATUSES = {"DONE", "CANCELLED", "FATAL"}


def to_system_datetime(iso_value):
	# Amazon returns UTC ISO 8601; store naive system-local like every other Datetime field.
	parsed = get_datetime(iso_value)
	if not parsed:
		return None
	if parsed.tzinfo:
		parsed = convert_utc_to_system_timezone(parsed).replace(tzinfo=None)
	return parsed


def get_sync_since_iso() -> str:
	latest = frappe.db.get_value(
		"Amazon Feed Ledger",
		{"feed_type": PRICING_FEED_TYPE},
		"created_time",
		order_by="created_time desc",
	)
	utc_now = datetime.utcnow()
	floor = utc_now - timedelta(days=MAX_LOOKBACK_DAYS)
	if not latest:
		since = utc_now - timedelta(hours=INITIAL_LOOKBACK_HOURS)
	else:
		# Elapsed between two system-local datetimes is tz-agnostic, so this avoids an
		# inverse-timezone conversion of the stored (system-local) created_time.
		elapsed = now_datetime() - get_datetime(latest)
		since = utc_now - elapsed - timedelta(hours=OVERLAP_HOURS)
	return max(since, floor).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_feeds(client, created_since: str) -> list:
	feeds = []
	next_token = None
	for _page in range(MAX_PAGES_PER_RUN):
		token = next_token

		def call():
			if token:
				return client.get_feeds(next_token=token)
			return client.get_feeds(
				feed_types=[PRICING_FEED_TYPE], page_size=PAGE_SIZE, created_since=created_since
			)

		response = retry_with_backoff(call, error_title="Amazon Feed Ledger Sync", throttle_only=True)
		feeds.extend(response.get("feeds") or [])
		next_token = response.get("nextToken")
		if not next_token:
			break
	return feeds


def upsert_feeds(feeds: list) -> int:
	feed_ids = [feed.get("feedId") for feed in feeds if feed.get("feedId")]
	if not feed_ids:
		return 0

	existing = set(frappe.get_all("Amazon Feed Ledger", filters={"name": ["in", feed_ids]}, pluck="name"))
	log_by_feed = {
		row.feed_id: row.name
		for row in frappe.get_all(
			"Amazon Pricing Submission Log",
			filters={"feed_id": ["in", feed_ids]},
			fields=["name", "feed_id"],
		)
		if row.feed_id
	}

	inserted = 0
	for feed in feeds:
		feed_id = feed.get("feedId")
		if not feed_id:
			continue

		submission_log = log_by_feed.get(feed_id)
		values = {
			"feed_type": feed.get("feedType"),
			"processing_status": feed.get("processingStatus"),
			"created_time": to_system_datetime(feed.get("createdTime")),
			"processing_end_time": to_system_datetime(feed.get("processingEndTime")),
			"result_document_id": feed.get("resultFeedDocumentId"),
			"source": "Navgold" if submission_log else "External",
			"submission_log": submission_log,
		}

		if feed_id in existing:
			frappe.db.set_value("Amazon Feed Ledger", feed_id, values)
		else:
			doc = frappe.new_doc("Amazon Feed Ledger")
			doc.feed_id = feed_id
			doc.update(values)
			doc.insert(ignore_permissions=True)
			inserted += 1

	frappe.db.commit()
	return inserted


def fetch_result_summary(client, result_document_id: str) -> dict:
	document = retry_with_backoff(
		lambda: client.get_feed_document(result_document_id),
		error_title="Amazon Feed Ledger Result",
		throttle_only=True,
	)
	content = download_binary(document["url"])
	if (document.get("compressionAlgorithm") or "").upper() == "GZIP":
		content = gzip.decompress(content)
	try:
		report = content.decode("utf-8")
	except UnicodeDecodeError:
		report = content.decode("cp1252")
	parsed = frappe.parse_json(report) or {}
	return parsed.get("summary") or {}


def fetch_missing_results(client) -> int:
	rows = frappe.get_all(
		"Amazon Feed Ledger",
		filters={
			"result_fetched": 0,
			"processing_status": "DONE",
			"result_document_id": ["is", "set"],
		},
		fields=["name", "result_document_id"],
		order_by="created_time desc",
		limit=MAX_RESULTS_PER_RUN,
	)

	fetched = 0
	for row in rows:
		try:
			summary = fetch_result_summary(client, row.result_document_id)
		except SPAPIError as error:
			# Throttled or transient — leave result_fetched=0 so the next run retries this feed.
			frappe.log_error(
				f"Feed result fetch failed for {row.name}: {error.error_description}",
				"Amazon Feed Ledger",
			)
			continue

		frappe.db.set_value(
			"Amazon Feed Ledger",
			row.name,
			{
				"messages_processed": cint(summary.get("messagesProcessed")),
				"messages_accepted": cint(summary.get("messagesAccepted")),
				"messages_invalid": cint(summary.get("messagesInvalid")),
				"result_fetched": 1,
			},
		)
		fetched += 1
		# Persist as we go so a throttle-kill mid-batch keeps the results already fetched.
		frappe.db.commit()  # nosemgrep

	return fetched


def run_sync() -> dict:
	client = get_feeds_client()
	created_since = get_sync_since_iso()
	feeds = fetch_feeds(client, created_since)
	inserted = upsert_feeds(feeds)
	results_fetched = fetch_missing_results(client)
	return {
		"created_since": created_since,
		"feeds_seen": len(feeds),
		"inserted": inserted,
		"results_fetched": results_fetched,
	}


@frappe.whitelist()
def sync_amazon_feed_ledger() -> dict:
	frappe.only_for("System Manager")
	return run_sync()
