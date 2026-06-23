# Copyright (c) 2026, Navgold and contributors
# For license information, please see license.txt

"""Nightly aggregation of Amazon orders into per-day stats for the dashboard report."""

import csv
import io

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, now_datetime, today

from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import Reports

ORDER_REPORT_TYPE = "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL"
DEFAULT_CURRENCY = "GBP"


def get_reports_client():
	settings_name = frappe.get_all("Amazon SP API Settings", filters={"is_active": 1}, pluck="name", limit=1)
	if not settings_name:
		frappe.throw(_("No active Amazon SP API Settings found"))
	settings = frappe.get_doc("Amazon SP API Settings", settings_name[0])
	return Reports.from_amazon_settings(settings)


def get_recent_window(days):
	# Returns (start_date, end_date); end_date is exclusive on the Amazon side, so
	# get_recent_window(1) is just yesterday and the window always spans exactly `days` days.
	end_date = getdate(add_days(today(), 1))
	start_date = getdate(add_days(end_date, -days))
	return start_date, end_date


def aggregate_daily_stats(report_text):
	reader = csv.DictReader(io.StringIO(report_text), delimiter="\t")
	if reader.fieldnames and "amazon-order-id" not in reader.fieldnames:
		frappe.throw(
			_("Amazon order report is missing expected columns; the report schema may have changed.")
		)

	# An order spans multiple item lines, so each order is counted once. gross_amount sums
	# non-cancelled lines only — Amazon leaves item-price populated on cancelled lines.
	stats_by_day = {}
	seen_orders = set()

	for row in reader:
		order_id = row.get("amazon-order-id")
		if not order_id:
			continue
		day = (row.get("purchase-date") or "")[:10]
		if not day:
			continue

		stat = stats_by_day.setdefault(
			day,
			{
				"total_orders": 0,
				"shipped_orders": 0,
				"cancelled_orders": 0,
				"gross_amount": 0.0,
				"currency": None,
			},
		)
		if not stat["currency"]:
			stat["currency"] = row.get("currency") or DEFAULT_CURRENCY

		status = (row.get("order-status") or "").strip().lower()
		is_cancelled = status.startswith("cancel")

		if not is_cancelled:
			stat["gross_amount"] = flt(stat["gross_amount"]) + flt(row.get("item-price"))

		if order_id in seen_orders:
			continue
		seen_orders.add(order_id)
		stat["total_orders"] += 1
		if status.startswith("shipped"):
			stat["shipped_orders"] += 1
		elif is_cancelled:
			stat["cancelled_orders"] += 1

	return stats_by_day


def save_daily_stats(stats_by_day):
	synced_at = now_datetime()
	existing = set(
		frappe.get_all("Amazon Daily Order Stat", filters={"name": ["in", list(stats_by_day)]}, pluck="name")
	)
	for day, stat in stats_by_day.items():
		if day in existing:
			doc = frappe.get_doc("Amazon Daily Order Stat", day)
		else:
			doc = frappe.new_doc("Amazon Daily Order Stat")
			doc.stat_date = getdate(day)
		doc.total_orders = stat["total_orders"]
		doc.shipped_orders = stat["shipped_orders"]
		doc.cancelled_orders = stat["cancelled_orders"]
		doc.gross_amount = stat["gross_amount"]
		doc.currency = stat["currency"] or DEFAULT_CURRENCY
		doc.last_synced = synced_at
		doc.save()
	# Persist nightly stats from the scheduled job.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	return len(stats_by_day)


def sync_stats_for_range(start_date=None, end_date=None):
	# Pull the Amazon orders report for the range, aggregate per day, and upsert one row per day.
	# end_date is exclusive on the Amazon side: pass the day after the last day you want included.
	end_date = getdate(end_date) if end_date else getdate(add_days(today(), 1))
	start_date = getdate(start_date) if start_date else getdate(add_days(end_date, -31))

	reports = get_reports_client()
	report_text = reports.request_and_download_report(
		ORDER_REPORT_TYPE,
		data_start_time=f"{start_date}T00:00:00Z",
		data_end_time=f"{end_date}T00:00:00Z",
	)
	stats_by_day = aggregate_daily_stats(report_text)
	saved = save_daily_stats(stats_by_day)
	return {"days_saved": saved, "from": str(start_date), "to": str(end_date)}


@frappe.whitelist()
def enqueue_backfill(days: int = 30):
	frappe.only_for("System Manager")
	days = cint(days) or 30
	start_date, end_date = get_recent_window(days)
	frappe.enqueue(
		"navgold_custom.navgold_custom.amazon_pricing.daily_order_stats.sync_stats_for_range",
		queue="long",
		timeout=1500,
		start_date=str(start_date),
		end_date=str(end_date),
	)
	frappe.msgprint(f"Backfill queued for the last {days} days. Stats will appear shortly.")


def sync_daily_order_stats():
	# Re-pull the last few days so late cancellations / status changes correct saved rows.
	start_date, end_date = get_recent_window(4)
	sync_stats_for_range(start_date=str(start_date), end_date=str(end_date))
