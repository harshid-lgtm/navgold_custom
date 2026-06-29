# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.data import add_to_date, cint, get_datetime, now_datetime, time_diff_in_seconds

EPOCH = get_datetime("1900-01-01 00:00:00")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{
			"label": _("Submission"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Amazon Pricing Submission Log",
			"width": 140,
		},
		{"label": _("Source"), "fieldname": "source", "fieldtype": "Data", "width": 90},
		{"label": _("Type"), "fieldname": "submission_type", "fieldtype": "Data", "width": 90},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
		{"label": _("Amazon Status"), "fieldname": "amazon_status", "fieldtype": "Data", "width": 120},
		{
			"label": _("Feed Ledger"),
			"fieldname": "ledger",
			"fieldtype": "Link",
			"options": "Amazon Feed Ledger",
			"width": 150,
		},
		{"label": _("Submitted At"), "fieldname": "submitted_at", "fieldtype": "Datetime", "width": 160},
		{"label": _("Completed At"), "fieldname": "completed_at", "fieldtype": "Datetime", "width": 160},
		{"label": _("Minutes Pending"), "fieldname": "minutes_pending", "fieldtype": "Int", "width": 120},
		{"label": _("Records"), "fieldname": "record_count", "fieldtype": "Int", "width": 90},
		{"label": _("Accepted"), "fieldname": "accepted", "fieldtype": "Int", "width": 90},
		{"label": _("Invalid"), "fieldname": "invalid", "fieldtype": "Int", "width": 90},
		{"label": _("Error"), "fieldname": "error_excerpt", "fieldtype": "Data", "width": 280},
	]


def get_data(filters):
	hours_back = cint(filters.get("hours_back")) or 48
	cutoff = add_to_date(now_datetime(), hours=-hours_back)
	source = filters.get("source")

	rows = []
	if source in (None, "", "Navgold"):
		rows.extend(get_navgold_rows(filters, cutoff))
	if source in (None, "", "External"):
		rows.extend(get_external_rows(filters, cutoff))

	rows.sort(key=lambda row: row.get("sort_time") or EPOCH, reverse=True)
	return rows


def get_navgold_rows(filters, cutoff):
	log_filters = {"creation": (">", cutoff)}
	if filters.get("submission_type"):
		log_filters["submission_type"] = filters.submission_type
	if filters.get("status"):
		log_filters["status"] = filters.status

	logs = frappe.get_all(
		"Amazon Pricing Submission Log",
		filters=log_filters,
		fields=[
			"name",
			"submission_type",
			"status",
			"amazon_status",
			"feed_id",
			"submitted_at",
			"completed_at",
			"error_message",
			"pricing_records",
		],
		order_by="creation desc",
		limit=0,
	)

	counts = get_ledger_counts([log.feed_id for log in logs if log.feed_id])
	now = now_datetime()
	rows = []
	for log in logs:
		minutes_pending = 0
		if log.status == "Submitted" and log.submitted_at:
			minutes_pending = cint(time_diff_in_seconds(now, log.submitted_at) / 60)

		count = counts.get(log.feed_id) or {}
		rows.append(
			{
				"name": log.name,
				"source": "Navgold",
				"submission_type": log.submission_type,
				"status": log.status,
				"amazon_status": log.amazon_status,
				"ledger": log.feed_id if log.feed_id in counts else None,
				"submitted_at": log.submitted_at,
				"completed_at": log.completed_at,
				"minutes_pending": minutes_pending,
				"record_count": count_lines(log.pricing_records),
				"accepted": count.get("messages_accepted"),
				"invalid": count.get("messages_invalid"),
				"error_excerpt": (log.error_message or "")[:200],
				"sort_time": log.submitted_at or get_datetime(log.completed_at),
			}
		)
	return rows


def get_external_rows(filters, cutoff):
	# Feeds Amazon received that no submission log matched — submitted directly by another
	# source (e.g. Repricer). The log-status / type filters are Navgold concepts, so when
	# either is set, external feeds are intentionally excluded.
	if filters.get("status") or filters.get("submission_type"):
		return []

	ledgers = frappe.get_all(
		"Amazon Feed Ledger",
		filters={"source": "External", "created_time": (">", cutoff)},
		fields=[
			"name",
			"feed_type",
			"processing_status",
			"created_time",
			"processing_end_time",
			"messages_processed",
			"messages_accepted",
			"messages_invalid",
		],
		order_by="created_time desc",
		limit=0,
	)

	rows = []
	for ledger in ledgers:
		invalid = cint(ledger.messages_invalid)
		rows.append(
			{
				"name": None,
				"source": "External",
				"submission_type": ledger.feed_type,
				"status": ledger.processing_status,
				"amazon_status": ledger.processing_status,
				"ledger": ledger.name,
				"submitted_at": ledger.created_time,
				"completed_at": ledger.processing_end_time,
				"minutes_pending": 0,
				"record_count": cint(ledger.messages_processed),
				"accepted": cint(ledger.messages_accepted),
				"invalid": invalid,
				"error_excerpt": _("{0} invalid").format(invalid) if invalid else "",
				"sort_time": ledger.created_time,
			}
		)
	return rows


def get_ledger_counts(feed_ids):
	if not feed_ids:
		return {}
	return {
		row.name: row
		for row in frappe.get_all(
			"Amazon Feed Ledger",
			filters={"name": ["in", feed_ids]},
			fields=["name", "messages_accepted", "messages_invalid"],
		)
	}


def count_lines(text):
	return len([line for line in (text or "").splitlines() if line.strip()])
