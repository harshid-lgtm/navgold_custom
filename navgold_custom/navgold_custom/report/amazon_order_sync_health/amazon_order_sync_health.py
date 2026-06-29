# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.data import add_to_date, cint, get_datetime, now_datetime


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data, summary = get_data_and_summary(filters)
	return columns, data, None, None, summary


def get_columns():
	return [
		{"label": _("Hour"), "fieldname": "hour_bucket", "fieldtype": "Data", "width": 160},
		{
			"label": _("Amazon Sales Orders"),
			"fieldname": "sales_order_count",
			"fieldtype": "Int",
			"width": 160,
		},
		{
			"label": _("Failed Syncs Created"),
			"fieldname": "failed_sync_count",
			"fieldtype": "Int",
			"width": 160,
		},
	]


def get_data_and_summary(filters):
	hours_back = cint(filters.get("hours_back")) or 48
	window_start = add_to_date(now_datetime(), hours=-hours_back)

	sales_order_times = frappe.get_all(
		"Sales Order",
		filters={"creation": (">", window_start), "amazon_order_id": ("is", "set")},
		pluck="creation",
		limit=0,
	)
	failed_sync_times = frappe.get_all(
		"Amazon Failed Sync Record",
		filters={"creation": (">", window_start)},
		pluck="creation",
		limit=0,
	)

	rows = build_hourly_buckets(sales_order_times, failed_sync_times)

	open_failed_count = frappe.db.count("Amazon Failed Sync Record", {"status": "Failed"})
	last_order_time = frappe.db.get_value(
		"Sales Order", {"amazon_order_id": ("is", "set")}, "creation", order_by="creation desc"
	)
	minutes_since_last_order = None
	if last_order_time:
		minutes_since_last_order = cint((now_datetime() - get_datetime(last_order_time)).total_seconds() / 60)

	summary = [
		{
			"label": _("Open Failed Syncs"),
			"value": open_failed_count,
			"indicator": "Red" if open_failed_count else "Green",
		},
		{
			"label": _("Minutes Since Last Amazon Order"),
			"value": minutes_since_last_order if minutes_since_last_order is not None else "-",
			"indicator": "Red" if (minutes_since_last_order or 0) > 120 else "Green",
		},
	]
	return rows, summary


def build_hourly_buckets(sales_order_times, failed_sync_times):
	buckets = {}
	for created_at in sales_order_times:
		key = get_datetime(created_at).strftime("%Y-%m-%d %H:00")
		buckets.setdefault(key, {"sales_order_count": 0, "failed_sync_count": 0})
		buckets[key]["sales_order_count"] += 1
	for created_at in failed_sync_times:
		key = get_datetime(created_at).strftime("%Y-%m-%d %H:00")
		buckets.setdefault(key, {"sales_order_count": 0, "failed_sync_count": 0})
		buckets[key]["failed_sync_count"] += 1

	return [{"hour_bucket": key, **counts} for key, counts in sorted(buckets.items(), reverse=True)]
