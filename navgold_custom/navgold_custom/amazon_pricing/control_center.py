# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.data import add_to_date, cint, flt, get_datetime, now_datetime

from navgold_custom.navgold_custom.amazon_pricing.alerts import alert_freeze_toggled
from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
	get_allowed_pricing_proxy_skus,
	get_zero_stock_proxy_skus,
)
from navgold_custom.navgold_custom.report.amazon_fba_stock_sync_health.amazon_fba_stock_sync_health import (
	get_data_and_summary as get_fba_stock_sync_health,
)
from navgold_custom.navgold_custom.report.amazon_order_sync_health.amazon_order_sync_health import (
	get_data_and_summary as get_order_sync_health,
)
from navgold_custom.navgold_custom.report.amazon_pricing_push_readiness.amazon_pricing_push_readiness import (
	get_data as get_push_readiness_data,
)
from navgold_custom.navgold_custom.report.amazon_top_selling_sku.amazon_top_selling_sku import (
	build_top_selling_rows,
)
from navgold_custom.navgold_custom.report.below_min_price_sales.below_min_price_sales import (
	get_data as get_below_min_data,
)

# Same violation strings the push-time validator emits, grouped for the at-a-glance counters.
VIOLATION_CATEGORIES = [
	("zero_or_missing_cost", ("Purchase cost is", "No active purchase cost row")),
	("strategy_issue", ("No active strategy", "does not exist", "is disabled")),
	("min_equals_max", ("equals max price",)),
	("min_above_max", ("is above max price",)),
	("invalid_min_max", ("Min price is", "Max price is")),
	("no_selling_price", ("No current selling price",)),
	("price_deviation", ("exceeds threshold",)),
]

CATEGORY_LABELS = {
	"zero_or_missing_cost": "Zero / missing purchase cost",
	"strategy_issue": "Strategy missing / invalid",
	"min_equals_max": "Min equals Max",
	"min_above_max": "Min above Max",
	"invalid_min_max": "Min/Max ≤ 0",
	"no_selling_price": "No selling-price baseline",
	"price_deviation": "Price deviation block",
}


def compute_loss_velocity(below_min_rows: list) -> dict:
	now = now_datetime()
	windows = {
		"15m": add_to_date(now, minutes=-15),
		"1h": add_to_date(now, hours=-1),
		"24h": add_to_date(now, hours=-24),
	}
	result = {key: {"count": 0, "loss": 0.0} for key in windows}

	for row in below_min_rows:
		created = row.get("creation")
		if not created:
			continue
		created = get_datetime(created)
		loss = flt(row.get("estimated_loss"))
		for key, cutoff in windows.items():
			if created >= cutoff:
				result[key]["count"] += 1
				result[key]["loss"] = flt(result[key]["loss"] + loss, 2)

	return result


def get_last_good_snapshot() -> dict | None:
	rows = frappe.get_all(
		"Amazon Pricing Snapshot",
		filters={"trigger_type": "Scheduled"},
		fields=["name", "snapshot_datetime", "item_count"],
		order_by="snapshot_datetime desc",
		limit=1,
	)
	if not rows:
		rows = frappe.get_all(
			"Amazon Pricing Snapshot",
			fields=["name", "snapshot_datetime", "item_count"],
			order_by="snapshot_datetime desc",
			limit=1,
		)
	if not rows:
		return None
	row = rows[0]
	return {"name": row.name, "datetime": str(row.snapshot_datetime), "item_count": row.item_count}


def get_push_status(switches: dict) -> dict:
	frozen = switches["freeze_amazon_outbound"]
	auto_on = switches["enable_pricing_sync"] and switches["amazon_pricing_enabled"]
	amazon_mode_sends_prices = switches["amazon_publishing_mode"] == "Stock and Prices"

	amazon_prices_live = bool(auto_on and not frozen and amazon_mode_sends_prices)
	amazon_stock_live = bool(auto_on and not frozen)
	repricer_live = bool(auto_on and not frozen and switches["repricer_enabled"])

	if frozen:
		headline = "FROZEN — nothing is leaving"
	elif not auto_on:
		headline = "Auto-push OFF — nothing is leaving automatically"
	elif amazon_prices_live:
		headline = "LIVE — prices ARE going to Amazon"
	else:
		bits = []
		if amazon_stock_live:
			bits.append("stock to Amazon")
		if repricer_live:
			bits.append("min/max to Repricer")
		headline = "Partial — " + (", ".join(bits) if bits else "nothing") + " (no prices to Amazon)"

	return {
		"headline": headline,
		"amazon_prices_live": amazon_prices_live,
		"amazon_stock_live": amazon_stock_live,
		"repricer_live": repricer_live,
		"frozen": bool(frozen),
		"auto_on": bool(auto_on),
		"amazon_mode": switches["amazon_publishing_mode"],
	}


def categorize_violation(text: str) -> set:
	categories = set()
	for key, needles in VIOLATION_CATEGORIES:
		if any(needle in text for needle in needles):
			categories.add(key)
	return categories


def get_oldest_open_feed_minutes(cutoff) -> int:
	# Feeds older than the window are un-reconciled orphans (no feed-status poller yet), not a live
	# stall, so the cutoff keeps them out of the current-health signal.
	oldest = frappe.db.get_value(
		"Amazon Pricing Submission Log",
		{"status": ["in", ["Pending", "Submitted"]], "creation": [">=", cutoff]},
		"creation",
		order_by="creation asc",
	)
	if not oldest:
		return 0
	return cint((now_datetime() - get_datetime(oldest)).total_seconds() / 60)


def get_outbound_scope(push_status: dict, readiness_rows: list) -> dict:
	allowed_pricing_proxy_skus = get_allowed_pricing_proxy_skus()
	zero_stock_proxy_skus = get_zero_stock_proxy_skus()
	scope = compute_outbound_scope(
		push_status, readiness_rows, allowed_pricing_proxy_skus, zero_stock_proxy_skus
	)
	scope["canary_skus"] = get_canary_sku_rows(allowed_pricing_proxy_skus)
	return scope


def compute_outbound_scope(
	push_status: dict,
	readiness_rows: list,
	allowed_pricing_proxy_skus: set | None,
	zero_stock_proxy_skus: set | None,
) -> dict:
	# The canary allowlist gates the whole feed, stock included (submit_active_pricing filters to it
	# before build_pricing_feed), so it bounds stock too — the single-writer boundary that leaves the
	# old system owning every listing outside canary.
	active_proxy_skus = {row["proxy_sku"] for row in readiness_rows if row.get("proxy_sku")}
	ready_proxy_skus = {
		row["proxy_sku"] for row in readiness_rows if row.get("proxy_sku") and not row.get("violations")
	}
	if allowed_pricing_proxy_skus is not None:
		stock_scope = active_proxy_skus & allowed_pricing_proxy_skus
		price_ready = ready_proxy_skus & allowed_pricing_proxy_skus
		canary_total = len(allowed_pricing_proxy_skus)
		canary_mode = "Selected"
	else:
		stock_scope = active_proxy_skus
		price_ready = ready_proxy_skus
		canary_total = len(active_proxy_skus)
		canary_mode = "All"

	# Suppressed SKUs still ride the feed but push 0 — not real stock — so count them apart.
	suppressed = stock_scope & zero_stock_proxy_skus if zero_stock_proxy_skus is not None else set()
	live_stock = stock_scope - suppressed

	return {
		"amazon_stock": len(live_stock) if push_status["amazon_stock_live"] else 0,
		"amazon_stock_suppressed": len(suppressed) if push_status["amazon_stock_live"] else 0,
		"amazon_price": len(price_ready) if push_status["amazon_prices_live"] else 0,
		"repricer_price": len(price_ready) if push_status["repricer_live"] else 0,
		"canary_total": canary_total,
		"canary_mode": canary_mode,
	}


def get_canary_sku_rows(allowed_pricing_proxy_skus: set | None) -> list:
	if not allowed_pricing_proxy_skus:
		return []
	return frappe.get_all(
		"Proxy SKU",
		filters={"name": ["in", list(allowed_pricing_proxy_skus)]},
		fields=["name as proxy_sku", "item", "asin", "is_fba"],
		order_by="name asc",
	)


def get_top_selling_skus(days_back: int = 30, top_n: int = 10) -> list:
	# Reuse the report builder so the panel and the report share one attribution rule.
	days_back = cint(days_back) or 30
	from_date = add_to_date(now_datetime(), days=-days_back)
	return build_top_selling_rows(from_date=from_date, top_n=cint(top_n) or 10, sort_by="Revenue")


@frappe.whitelist()
def get_control_center_data(below_min_days: int = 3, top_n: int = 30, top_sellers_days: int = 30) -> dict:
	below_min_days = cint(below_min_days) or 3
	top_n = cint(top_n) or 30
	top_sellers_days = cint(top_sellers_days) or 30

	settings = frappe.get_cached_doc("Navgold Settings")
	amazon_settings = frappe.get_cached_doc("Amazon SP API Settings")

	switches = {
		"freeze_amazon_outbound": cint(settings.get("freeze_amazon_outbound")),
		"amazon_publishing_mode": settings.get("amazon_publishing_mode") or "—",
		"pricing_publishing_mode": settings.get("pricing_publishing_mode") or "—",
		"send_min_price_to_amazon": cint(settings.get("send_min_price_to_amazon")),
		"enable_afn_stock_sync": cint(settings.get("enable_afn_stock_sync")),
		"enable_pricing_sync": cint(settings.get("enable_pricing_sync")),
		"amazon_pricing_enabled": cint(settings.get("amazon_pricing_enabled")),
		"repricer_enabled": cint(settings.get("repricer_enabled")),
		"order_sync_enabled": cint(amazon_settings.get("enable_sync")),
		"price_change_threshold_percent": flt(settings.get("price_change_threshold_percent")),
		"price_change_block_direction": settings.get("price_change_block_direction") or "Both",
		"last_pricing_synced_at": str(settings.get("last_pricing_synced_at") or ""),
	}

	push_status = get_push_status(switches)

	# Reuse the report's data builder so the control center matches the push-time validator.
	readiness_rows = get_push_readiness_data({})
	total_active = len(readiness_rows)
	blocked_rows = [row for row in readiness_rows if row.get("violations")]

	category_counts = {key: 0 for key, _needles in VIOLATION_CATEGORIES}
	for row in blocked_rows:
		for category in categorize_violation(row["violations"]):
			category_counts[category] += 1

	# Zero-cost offenders first, then alphabetical.
	top_offenders = sorted(
		blocked_rows,
		key=lambda row: (flt(row.get("purchase_cost")) > 0, row.get("proxy_sku") or ""),
	)[:top_n]

	below_min_rows = get_below_min_data({"days_back": below_min_days, "only_below_min": 1})
	below_min_loss = sum(flt(row.get("estimated_loss")) for row in below_min_rows)
	loss_velocity = compute_loss_velocity(below_min_rows)
	worst_losses = sorted(below_min_rows, key=lambda row: flt(row.get("estimated_loss")), reverse=True)[
		:top_n
	]

	window_hours = cint(settings.get("feed_health_window_hours")) or 48
	feed_health_cutoff = add_to_date(now_datetime(), hours=-window_hours)
	feed_status_counts = {
		status: frappe.db.count(
			"Amazon Pricing Submission Log", {"status": status, "creation": [">=", feed_health_cutoff]}
		)
		for status in ("Pending", "Submitted", "Error")
	}
	recent_feeds = frappe.get_all(
		"Amazon Pricing Submission Log",
		fields=["name", "submission_type", "status", "amazon_status", "blocked_count", "creation"],
		order_by="creation desc",
		limit=8,
	)
	_order_rows, order_summary = get_order_sync_health({"hours_back": 48})
	fba_stock_rows, fba_stock_summary = get_fba_stock_sync_health({})
	# Lowest fulfillable stock first — the out-of-stock / about-to-stock-out SKUs are the actionable ones.
	fba_stock_rows = sorted(fba_stock_rows, key=lambda row: flt(row.get("afn_stock")))[:top_n]

	alerts = {
		"telegram_enabled": cint(settings.get("enable_telegram_alerts")),
		"telegram_configured": bool(settings.get("telegram_bot_token") and settings.get("telegram_chat_id")),
	}

	return {
		"alerts": alerts,
		"push_status": push_status,
		"generated_on": str(now_datetime()),
		"switches": switches,
		"readiness": {
			"total_active": total_active,
			"blocked_total": len(blocked_rows),
			"ok_total": total_active - len(blocked_rows),
			"categories": category_counts,
			"category_labels": CATEGORY_LABELS,
			"top_offenders": top_offenders,
		},
		"below_min": {
			"count": len(below_min_rows),
			"estimated_loss": flt(below_min_loss, 2),
			"days_back": below_min_days,
			"velocity": loss_velocity,
			"rows": worst_losses,
		},
		"last_good_snapshot": get_last_good_snapshot(),
		"top_sellers": {
			"days_back": top_sellers_days,
			"rows": get_top_selling_skus(top_sellers_days, top_n=10),
		},
		"feeds": {
			"status_counts": feed_status_counts,
			"recent": recent_feeds,
			"oldest_open_minutes": get_oldest_open_feed_minutes(feed_health_cutoff),
			"window_hours": window_hours,
		},
		"outbound_scope": get_outbound_scope(push_status, readiness_rows),
		"orders": {"summary": order_summary},
		"fba_stock": {"summary": fba_stock_summary, "rows": fba_stock_rows},
	}


@frappe.whitelist()
def set_outbound_freeze(freeze: int) -> int:
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can change the outbound freeze switch."))

	freeze = cint(freeze)
	frappe.db.set_single_value("Navgold Settings", "freeze_amazon_outbound", freeze)
	frappe.clear_cache(doctype="Navgold Settings")

	from navgold_custom.navgold_custom.amazon_pricing.alerts import alert_freeze_toggled

	alert_freeze_toggled(freeze)
	return freeze


@frappe.whitelist()
def emergency_rollback(channel: str = "Repricer") -> dict:
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can run an emergency rollback."))

	snapshot = get_last_good_snapshot()
	if not snapshot:
		frappe.throw(_("No pricing snapshot exists to roll back to."))

	doc = frappe.get_doc("Amazon Pricing Snapshot", snapshot["name"])
	revert_result = doc.revert_prices()
	publish_result = doc.publish(channel)
	return {"snapshot": snapshot["name"], "revert": revert_result, "publish": publish_result}


@frappe.whitelist()
def contain_now() -> dict:
	# One-button incident response: restore last-good prices to BOTH channels, then freeze.
	# Publish happens BEFORE the freeze so the freeze can't block the corrective push — a
	# freeze alone only stops new prices, it never heals a bad price already live on Amazon.
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can run an emergency containment."))

	snapshot = get_last_good_snapshot()
	if not snapshot:
		frappe.throw(_("No pricing snapshot exists to roll back to."))

	doc = frappe.get_doc("Amazon Pricing Snapshot", snapshot["name"])
	revert_result = doc.revert_prices()

	frappe.db.set_single_value("Navgold Settings", "freeze_amazon_outbound", 0)
	frappe.clear_cache(doctype="Navgold Settings")
	try:
		repricer_result = doc.publish("Repricer")
		amazon_result = doc.publish("Amazon")
	finally:
		frappe.db.set_single_value("Navgold Settings", "freeze_amazon_outbound", 1)
		frappe.clear_cache(doctype="Navgold Settings")
		alert_freeze_toggled(1)

	return {
		"snapshot": snapshot["name"],
		"revert": revert_result,
		"repricer": repricer_result,
		"amazon": amazon_result,
		"frozen": 1,
	}
