# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count, Sum
from frappe.utils import add_to_date, now_datetime, time_diff_in_seconds
from frappe.utils.caching import redis_cache
from frappe.utils.data import cint, flt

from navgold_custom.navgold_custom.report.below_min_price_sales.below_min_price_sales import (
	build_below_min_rows,
	get_min_price_map,
)

_TOGGLEABLE_FLAGS = frozenset(
	{
		"freeze_amazon_outbound",
		"enable_pricing_sync",
		"repricer_enabled",
		"pause_strategy_refresh",
		"enable_afn_stock_sync",
	}
)


# ---------------------------------------------------------------------------
# Heavy helpers — cached so the dashboard can be polled every 30 s safely
# ---------------------------------------------------------------------------


@redis_cache(ttl=120)
def get_readiness_blocked_count() -> int:
	"""Count active Amazon Product Pricing records that have at least one violation.

	Called from get_control_center_status and cached for 120 s because
	collect_pricing_violations does O(n) DB work across ~8k SKUs.
	"""
	from navgold_custom.navgold_custom.amazon_pricing.pricing_validators import (
		collect_pricing_violations,
	)

	pricing_names = frappe.get_all(
		"Amazon Product Pricing",
		filters={"is_active": 1},
		pluck="name",
	)
	if not pricing_names:
		return 0

	violations = collect_pricing_violations(pricing_names, include_deviation=False)
	return len(violations)


@redis_cache(ttl=60)
def get_below_min_24h_count() -> int:
	"""Count Amazon SO line items in the last 24 h where the rate < proxy min price.

	Reuses the query helpers from the Below Min Price Sales report rather than
	duplicating the join logic.
	"""
	sales_order = DocType("Sales Order")
	sales_order_item = DocType("Sales Order Item")

	cutoff = add_to_date(now_datetime(), hours=-24)
	so_rows = (
		frappe.qb.from_(sales_order_item)
		.join(sales_order)
		.on(sales_order_item.parent == sales_order.name)
		.select(
			sales_order.name.as_("sales_order"),
			sales_order.creation,
			sales_order_item.item_code,
			sales_order_item.uom,
			sales_order_item.qty,
			sales_order_item.rate,
		)
		.where(
			(sales_order.docstatus == 1)
			& (sales_order.amazon_order_id.isnotnull())
			& (sales_order.creation > cutoff)
		)
	).run(as_dict=True)

	if not so_rows:
		return 0

	min_price_map = get_min_price_map({(row.item_code, row.uom) for row in so_rows})
	below_min_rows = build_below_min_rows(so_rows, min_price_map)
	# Only count rows that actually sold below min (deficit > 0)
	return sum(1 for row in below_min_rows if row["deficit"] > 0)


@redis_cache(ttl=60)
def get_order_analytics_data(hours: int) -> dict:
	"""Compute order analytics over a rolling window.

	Cached per hours value so sequential dashboard polls don't hammer the DB.
	"""
	cutoff = add_to_date(now_datetime(), hours=-hours)
	past_hour_cutoff = add_to_date(now_datetime(), hours=-1)

	sales_order = DocType("Sales Order")
	sales_order_item = DocType("Sales Order Item")
	item_table = DocType("Item")

	# Card totals — two window queries, one round-trip each
	period_rows = (
		frappe.qb.from_(sales_order)
		.select(sales_order.name, sales_order.grand_total, sales_order.creation)
		.where(
			(sales_order.docstatus == 1)
			& (sales_order.amazon_order_id.isnotnull())
			& (sales_order.creation >= cutoff)
		)
	).run(as_dict=True)

	orders_period = len(period_rows)
	sales_period = flt(sum(flt(row.grand_total) for row in period_rows), 2)

	past_hour_rows = [row for row in period_rows if row.creation >= past_hour_cutoff]
	orders_past_hour = len(past_hour_rows)
	sales_past_hour = flt(sum(flt(row.grand_total) for row in past_hour_rows), 2)

	period_so_names = [row.name for row in period_rows]

	# Order type pie — one query
	order_type_rows = (
		frappe.qb.from_(sales_order)
		.select(
			sales_order.fulfillment_channel,
			sales_order.is_prime,
			sales_order.amazon_customer_type,
			Count(sales_order.name).as_("order_count"),
		)
		.where(
			(sales_order.docstatus == 1)
			& (sales_order.amazon_order_id.isnotnull())
			& (sales_order.creation >= cutoff)
		)
		.groupby(
			sales_order.fulfillment_channel,
			sales_order.is_prime,
			sales_order.amazon_customer_type,
		)
	).run(as_dict=True)

	order_type_buckets: dict[str, int] = {}
	for row in order_type_rows:
		if row.fulfillment_channel == "AFN":
			label = "FBA"
		elif cint(row.is_prime):
			label = "Prime (MFN)"
		elif row.amazon_customer_type == "B2B":
			label = "B2B"
		else:
			label = "Non-Prime (MFN)"
		order_type_buckets[label] = order_type_buckets.get(label, 0) + cint(row.order_count)

	order_type_pie = [{"label": label, "value": value} for label, value in order_type_buckets.items()]

	# Top categories and top items — only meaningful if there are orders in window
	top_categories: list[dict] = []
	top_items: list[dict] = []

	if period_so_names:
		# Chunk to avoid >1000-item IN lists (MariaDB limit)
		category_accum: dict[str, dict] = {}
		item_accum: dict[str, dict] = {}

		chunk_size = 500
		for offset in range(0, len(period_so_names), chunk_size):
			chunk = period_so_names[offset : offset + chunk_size]

			category_rows = (
				frappe.qb.from_(sales_order_item)
				.join(item_table)
				.on(sales_order_item.item_code == item_table.name)
				.select(
					item_table.item_group.as_("label"),
					Sum(sales_order_item.qty).as_("qty"),
					Sum(sales_order_item.amount).as_("amount"),
				)
				.where(sales_order_item.parent.isin(chunk))
				.groupby(item_table.item_group)
			).run(as_dict=True)

			for row in category_rows:
				key = row.label
				existing = category_accum.get(key)
				if existing:
					existing["qty"] = flt(existing["qty"]) + flt(row.qty)
					existing["amount"] = flt(existing["amount"]) + flt(row.amount)
				else:
					category_accum[key] = {"label": key, "qty": flt(row.qty), "amount": flt(row.amount)}

			item_rows = (
				frappe.qb.from_(sales_order_item)
				.select(
					sales_order_item.item_code.as_("label"),
					Sum(sales_order_item.qty).as_("qty"),
					Sum(sales_order_item.amount).as_("amount"),
				)
				.where(sales_order_item.parent.isin(chunk))
				.groupby(sales_order_item.item_code)
			).run(as_dict=True)

			for row in item_rows:
				key = row.label
				existing = item_accum.get(key)
				if existing:
					existing["qty"] = flt(existing["qty"]) + flt(row.qty)
					existing["amount"] = flt(existing["amount"]) + flt(row.amount)
				else:
					item_accum[key] = {"label": key, "qty": flt(row.qty), "amount": flt(row.amount)}

		top_categories = sorted(category_accum.values(), key=lambda r: r["amount"], reverse=True)[:10]
		top_items = sorted(item_accum.values(), key=lambda r: r["amount"], reverse=True)[:10]

	# Hourly series — bucket by hour using Python to avoid fragile SQL date_format differences
	hourly_buckets: dict[str, dict] = {}
	# Pre-build all hour slots so the chart has a continuous x-axis even in empty windows
	for hour_offset in range(hours):
		slot_dt = add_to_date(cutoff, hours=hour_offset)
		slot_key = slot_dt.strftime("%Y-%m-%dT%H:00:00")
		hourly_buckets[slot_key] = {"ts": slot_key, "orders": 0, "sales": flt(0)}

	for row in period_rows:
		slot_key = row.creation.strftime("%Y-%m-%dT%H:00:00")
		if slot_key in hourly_buckets:
			hourly_buckets[slot_key]["orders"] += 1
			hourly_buckets[slot_key]["sales"] = flt(
				flt(hourly_buckets[slot_key]["sales"]) + flt(row.grand_total), 2
			)

	hourly_series = [hourly_buckets[key] for key in sorted(hourly_buckets)]

	return {
		"cards": {
			"orders_past_hour": orders_past_hour,
			"sales_past_hour": sales_past_hour,
			"orders_period": orders_period,
			"sales_period": sales_period,
		},
		"order_type_pie": order_type_pie,
		"top_categories": top_categories,
		"top_items": top_items,
		"hourly_series": hourly_series,
	}


@redis_cache(ttl=30)
def get_poll_blockers() -> dict:
	"""Count the four poll blockers in one cached helper.

	get_control_center_status is polled every 30 s by any logged-in dashboard, so these
	otherwise-uncached Count(...) queries (against Submission Log / Error Log / Sales Order)
	would run on every poll for every viewer. Cached for the poll interval. Freeze/approval
	flags are read live in the caller and are NOT cached here.
	"""
	submission_log = DocType("Amazon Pricing Submission Log")
	stuck_cutoff = add_to_date(now_datetime(), minutes=-60)
	stuck_feeds = (
		frappe.qb.from_(submission_log)
		.select(Count(submission_log.name))
		.where((submission_log.status == "Submitted") & (submission_log.submitted_at < stuck_cutoff))
	).run()[0][0] or 0

	open_failed_syncs = (
		frappe.qb.from_(submission_log)
		.select(Count(submission_log.name))
		.where(submission_log.status == "Error")
	).run()[0][0] or 0

	error_log = DocType("Error Log")
	errors_1h_cutoff = add_to_date(now_datetime(), hours=-1)
	errors_1h = (
		frappe.qb.from_(error_log).select(Count(error_log.name)).where(error_log.creation > errors_1h_cutoff)
	).run()[0][0] or 0

	sales_order = DocType("Sales Order")
	last_order_row = (
		frappe.qb.from_(sales_order)
		.select(sales_order.creation)
		.where((sales_order.docstatus == 1) & (sales_order.amazon_order_id.isnotnull()))
		.orderby(sales_order.creation, order=frappe.qb.desc)
		.limit(1)
	).run(as_dict=True)

	if last_order_row:
		minutes_since_last_order = cint(time_diff_in_seconds(now_datetime(), last_order_row[0].creation) / 60)
	else:
		minutes_since_last_order = None

	return {
		"open_failed_syncs": cint(open_failed_syncs),
		"stuck_feeds": cint(stuck_feeds),
		"errors_1h": cint(errors_1h),
		"minutes_since_last_order": minutes_since_last_order,
	}


# ---------------------------------------------------------------------------
# Whitelisted API endpoints
# ---------------------------------------------------------------------------


def get_latest_usable_snapshot() -> dict | None:
	"""Latest captured/published snapshot, mapped to the control-center payload shape.

	The dashboard's PanicCard reads name/taken_at/sku_count/status, so the JSON snapshot
	doctype's snapshot_datetime/item_count are mapped to that stable contract here.
	"""
	rows = frappe.get_all(
		"Amazon Pricing Snapshot",
		filters={"item_count": [">", 0]},
		fields=["name", "snapshot_datetime", "item_count", "status"],
		order_by="snapshot_datetime desc",
		limit=1,
	)
	if not rows:
		return None
	row = rows[0]
	return {
		"name": row.name,
		"taken_at": row.snapshot_datetime,
		"sku_count": row.item_count,
		"status": row.status,
	}


@frappe.whitelist()
def get_control_center_status() -> dict:
	"""Return the complete control-center status payload.

	Read-only — no mutation, no permission guard beyond being logged-in.
	"""
	navgold_settings = frappe.get_single("Navgold Settings")
	amazon_settings_list = frappe.get_all(
		"Amazon SP API Settings",
		filters={"enable_sync": 1},
		fields=["name", "enable_sync"],
		limit=1,
	)
	enable_sync = 1 if amazon_settings_list else 0

	poll_blockers = get_poll_blockers()
	snapshot = get_latest_usable_snapshot()

	return {
		"flags": {
			"enable_pricing_sync": cint(navgold_settings.enable_pricing_sync),
			"repricer_enabled": cint(navgold_settings.repricer_enabled),
			"freeze_amazon_outbound": cint(navgold_settings.freeze_amazon_outbound),
			"pause_strategy_refresh": cint(navgold_settings.pause_strategy_refresh),
			"enable_afn_stock_sync": cint(navgold_settings.enable_afn_stock_sync),
			"amazon_publishing_mode": navgold_settings.amazon_publishing_mode,
			"enable_sync": enable_sync,
		},
		"blockers": {
			"readiness_blocked": get_readiness_blocked_count(),
			"below_min_24h": get_below_min_24h_count(),
			"open_failed_syncs": poll_blockers["open_failed_syncs"],
			"stuck_feeds": poll_blockers["stuck_feeds"],
			"errors_1h": poll_blockers["errors_1h"],
			"minutes_since_last_order": poll_blockers["minutes_since_last_order"],
		},
		"snapshot": snapshot,
	}


@frappe.whitelist()
def get_order_analytics(hours: int = 24) -> dict:
	hours = cint(hours) or 24
	return get_order_analytics_data(hours)


@frappe.whitelist()
def capture_price_snapshot() -> dict:
	"""Capture a known-good price set via the Amazon Pricing Snapshot doctype.

	Reuses the JSON snapshot's capture_snapshot helper (one get_all, no per-row work),
	so there is no background job or duplicated capture logic.
	"""
	frappe.only_for("System Manager")

	from navgold_custom.navgold_custom.doctype.amazon_pricing_snapshot.amazon_pricing_snapshot import (
		capture_snapshot,
	)

	snapshot_name = capture_snapshot(trigger="Manual", notes="Control center capture")
	return {"snapshot": snapshot_name}


@frappe.whitelist()
def panic_button(restore: int = 0) -> dict:
	"""Emergency stop — halts all Amazon outbound and optionally restores last snapshot.

	ORDER IS CRITICAL:
	1. Disable sync flags immediately.
	2. If restore requested, revert ERP prices from the latest snapshot and publish that
	   restore feed BEFORE setting the freeze flag (freeze blocks feed submission).
	3. Set freeze LAST, in a finally block so it always lands even if restore fails.
	"""
	frappe.only_for("System Manager")

	restore = cint(restore)
	stopped_flags = []
	restored_skus = 0
	log_name = None

	# Step 1 — disable sync flags. set_single_value invalidates the single's cache itself,
	# so no extra fetch-then-clear_cache is needed here.
	for fieldname, value in [
		("enable_pricing_sync", 0),
		("repricer_enabled", 0),
		("pause_strategy_refresh", 1),
	]:
		frappe.db.set_single_value("Navgold Settings", fieldname, value)
		stopped_flags.append(fieldname)

	frappe.log_error(
		f"PANIC: stopped flags {stopped_flags} by {frappe.session.user}",
		"Amazon Panic Button",
	)

	try:
		if restore:
			latest_snapshot_rows = frappe.get_all(
				"Amazon Pricing Snapshot",
				filters={"item_count": [">", 0]},
				fields=["name"],
				order_by="snapshot_datetime desc",
				limit=1,
			)

			if latest_snapshot_rows:
				snapshot_name = latest_snapshot_rows[0].name
				log_name, restored_skus = submit_restore_feed_from_snapshot(snapshot_name)
				frappe.log_error(
					f"PANIC RESTORE: published {restored_skus} SKUs from snapshot {snapshot_name}, log={log_name}",
					"Amazon Panic Button",
				)
			else:
				frappe.log_error(
					"PANIC RESTORE requested but no snapshot with captured prices found",
					"Amazon Panic Button",
				)

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Amazon Panic Restore Failed")
		# Do not re-raise — freeze must always land

	finally:
		# Step 3 — freeze last so the restore feed above can slip through.
		frappe.db.set_single_value("Navgold Settings", "freeze_amazon_outbound", 1)
		frappe.log_error(
			f"PANIC: freeze_amazon_outbound set to 1 by {frappe.session.user}",
			"Amazon Panic Button",
		)

	return {
		"stopped": stopped_flags,
		"restored_skus": restored_skus,
		"frozen": 1,
		"log": log_name,
	}


def submit_restore_feed_from_snapshot(snapshot_name: str) -> tuple[str | None, int]:
	"""Restore prices from an Amazon Pricing Snapshot and publish them to Amazon.

	Reuses the snapshot doctype's revert_prices (writes the captured min/max/strategy back
	to the ERP pricing records) followed by publish("Amazon"), which goes through the
	canonical amazon_feed_submitter / amazon_feed_builder path. This guarantees the restore
	feed is byte-for-byte the same shape as a normal scheduled pricing feed.

	Returns (log_name, sku_count). log_name may be None when nothing was eligible to publish.
	Raises on submission error so panic_button can catch it without masking the freeze step.
	"""
	snapshot = frappe.get_doc("Amazon Pricing Snapshot", snapshot_name)

	revert_result = snapshot.revert_prices() or {}
	publish_result = snapshot.publish("Amazon") or {}

	restored_skus = cint(revert_result.get("reverted"))
	log_name = publish_result.get("log_name")

	return log_name, restored_skus


@frappe.whitelist()
def toggle_flag(fieldname: str, value: int) -> dict:
	"""Toggle a single Navgold Settings flag by name.

	Only the fields in _TOGGLEABLE_FLAGS are permitted — any other fieldname
	is rejected to prevent arbitrary single-table writes through this endpoint.
	"""
	frappe.only_for("System Manager")

	if fieldname not in _TOGGLEABLE_FLAGS:
		frappe.throw(
			_("Field {0} cannot be toggled via this endpoint. Allowed fields: {1}").format(
				fieldname, ", ".join(sorted(_TOGGLEABLE_FLAGS))
			)
		)

	value = cint(value)
	# set_single_value invalidates the single's cache itself — no extra clear_cache needed,
	# matching the panic_button path.
	frappe.db.set_single_value("Navgold Settings", fieldname, value)

	# Re-read to confirm the DB write landed
	confirmed_value = cint(frappe.db.get_single_value("Navgold Settings", fieldname))
	return {fieldname: confirmed_value}
