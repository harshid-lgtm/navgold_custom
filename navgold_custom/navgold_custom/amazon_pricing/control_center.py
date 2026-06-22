# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.utils.data import add_to_date, cint, flt, get_datetime, now_datetime

from navgold_custom.navgold_custom.report.amazon_order_sync_health.amazon_order_sync_health import (
	get_data_and_summary as get_order_sync_health,
)
from navgold_custom.navgold_custom.report.amazon_pricing_push_readiness.amazon_pricing_push_readiness import (
	get_data as get_push_readiness_data,
)
from navgold_custom.navgold_custom.report.below_min_price_sales.below_min_price_sales import (
	get_data as get_below_min_data,
)
from navgold_custom.navgold_custom.report.supplier_data_readiness.supplier_data_readiness import (
	get_data as get_supplier_readiness_data,
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
	# Rate of bleed is the fire gauge: a count over 3 days is calm, the same loss in 15 minutes
	# is an evacuation. Bucket the below-min sales by how recently they happened.
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


def build_push_status(switches: dict) -> dict:
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


@frappe.whitelist()
def get_control_center_data(below_min_days: int = 3, top_n: int = 30) -> dict:
	below_min_days = cint(below_min_days) or 3
	top_n = cint(top_n) or 30

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

	push_status = build_push_status(switches)

	# Pricing readiness — reuse the report's data builder so the control center can never
	# disagree with what the push-time validator will actually block.
	readiness_rows = get_push_readiness_data({})
	total_active = len(readiness_rows)
	blocked_rows = [row for row in readiness_rows if row.get("violations")]

	category_counts = {key: 0 for key, _needles in VIOLATION_CATEGORIES}
	for row in blocked_rows:
		for category in categorize_violation(row["violations"]):
			category_counts[category] += 1

	# Zero-cost offenders first (the June-11 condition), then alphabetical.
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

	feed_status_counts = {
		"Pending": frappe.db.count("Amazon Pricing Submission Log", {"status": "Pending"}),
		"Submitted": frappe.db.count("Amazon Pricing Submission Log", {"status": "Submitted"}),
		"Error": frappe.db.count("Amazon Pricing Submission Log", {"status": "Error"}),
	}
	recent_feeds = frappe.get_all(
		"Amazon Pricing Submission Log",
		fields=["name", "submission_type", "status", "amazon_status", "blocked_count", "creation"],
		order_by="creation desc",
		limit=8,
	)

	_rows, order_summary = get_order_sync_health({"hours_back": 48})

	supplier_rows = get_supplier_readiness_data({})
	stale_suppliers = [supplier for supplier in supplier_rows if supplier.get("is_stale")]

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
		"feeds": {"status_counts": feed_status_counts, "recent": recent_feeds},
		"orders": {"summary": order_summary},
		"suppliers": {
			"stale_count": len(stale_suppliers),
			"total": len(supplier_rows),
			"rows": stale_suppliers[:top_n],
		},
	}


@frappe.whitelist()
def set_outbound_freeze(freeze: int) -> int:
	if "System Manager" not in frappe.get_roles():
		frappe.throw("Only a System Manager can change the outbound freeze switch.")

	freeze = cint(freeze)
	frappe.db.set_single_value("Navgold Settings", "freeze_amazon_outbound", freeze)
	frappe.clear_cache(doctype="Navgold Settings")

	from navgold_custom.navgold_custom.amazon_pricing.alerts import alert_freeze_toggled

	alert_freeze_toggled(freeze)
	return freeze


@frappe.whitelist()
def emergency_rollback(channel: str = "Repricer") -> dict:
	# One action during a fire: restore the last known-good prices and re-publish them.
	if "System Manager" not in frappe.get_roles():
		frappe.throw("Only a System Manager can run an emergency rollback.")

	snapshot = get_last_good_snapshot()
	if not snapshot:
		frappe.throw("No pricing snapshot exists to roll back to.")

	doc = frappe.get_doc("Amazon Pricing Snapshot", snapshot["name"])
	revert_result = doc.revert_prices()
	publish_result = doc.publish(channel)
	return {"snapshot": snapshot["name"], "revert": revert_result, "publish": publish_result}
