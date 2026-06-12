# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.data import add_to_date, cint, now_datetime, time_diff_in_seconds


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
		{"label": _("Type"), "fieldname": "submission_type", "fieldtype": "Data", "width": 90},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
		{"label": _("Amazon Status"), "fieldname": "amazon_status", "fieldtype": "Data", "width": 120},
		{"label": _("Feed ID"), "fieldname": "feed_id", "fieldtype": "Data", "width": 130},
		{"label": _("Submitted At"), "fieldname": "submitted_at", "fieldtype": "Datetime", "width": 160},
		{"label": _("Completed At"), "fieldname": "completed_at", "fieldtype": "Datetime", "width": 160},
		{"label": _("Minutes Pending"), "fieldname": "minutes_pending", "fieldtype": "Int", "width": 120},
		{"label": _("Records"), "fieldname": "record_count", "fieldtype": "Int", "width": 90},
		{"label": _("Error"), "fieldname": "error_excerpt", "fieldtype": "Data", "width": 300},
	]


def get_data(filters):
	hours_back = cint(filters.get("hours_back")) or 48
	log_filters = {"creation": (">", add_to_date(now_datetime(), hours=-hours_back))}
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

	now = now_datetime()
	rows = []
	for log in logs:
		minutes_pending = 0
		if log.status == "Submitted" and log.submitted_at:
			minutes_pending = cint(time_diff_in_seconds(now, log.submitted_at) / 60)

		rows.append(
			{
				"name": log.name,
				"submission_type": log.submission_type,
				"status": log.status,
				"amazon_status": log.amazon_status,
				"feed_id": log.feed_id,
				"submitted_at": log.submitted_at,
				"completed_at": log.completed_at,
				"minutes_pending": minutes_pending,
				"record_count": len(
					[line for line in (log.pricing_records or "").splitlines() if line.strip()]
				),
				"error_excerpt": (log.error_message or "")[:200],
			}
		)
	return rows
