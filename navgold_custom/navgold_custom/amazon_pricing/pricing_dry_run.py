# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

"""
Pricing dry run: calculate every active SKU's price, render the exact JSON feed that a real
sync would push, and surface what changed versus the previous snapshot — without ever pushing
to Amazon. Born from the go-live incident where prices went out with no rendered-feed review.
"""

import frappe
from frappe import _
from frappe.utils.data import cint, cstr, flt

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
	build_pricing_feed,
	get_allowed_pricing_proxy_skus,
	validate_pricing_feed,
)


def get_price_change_threshold() -> float:
	return flt(
		frappe.get_cached_value("Navgold Settings", "Navgold Settings", "price_change_threshold_percent")
	)


def get_previous_snapshot() -> dict | None:
	# Baseline against the most recent real price set, never another dry run — otherwise running
	# two dry runs back to back would diff against each other and hide the real movement.
	previous = frappe.get_all(
		"Amazon Pricing Snapshot",
		filters={"item_count": [">", 0], "trigger_type": ["!=", "Dry Run"]},
		fields=["name", "snapshot_data"],
		order_by="snapshot_datetime desc",
		limit=1,
	)
	return previous[0] if previous else None


def delta_pct(previous_value: float, new_value: float) -> float:
	previous_value = flt(previous_value)
	if not previous_value:
		return 0.0
	return (flt(new_value) - previous_value) / previous_value * 100


def compute_price_variance(current_rows: list[dict]) -> dict:
	previous_snapshot = get_previous_snapshot()
	threshold = get_price_change_threshold()

	previous_map = {}
	if previous_snapshot:
		for row in frappe.parse_json(previous_snapshot.snapshot_data or "[]"):
			previous_map[row.get("pricing")] = row

	rows = []
	movers = 0
	new_count = 0
	current_keys = set()
	for row in current_rows:
		pricing = row.get("pricing")
		current_keys.add(pricing)
		previous = previous_map.get(pricing)
		min_delta = max_delta = 0.0
		is_new = previous is None
		if previous:
			min_delta = delta_pct(previous.get("min_price"), row.get("min_price"))
			max_delta = delta_pct(previous.get("max_price"), row.get("max_price"))

		flagged = bool(threshold) and (abs(min_delta) > threshold or abs(max_delta) > threshold)
		if is_new:
			new_count += 1
			status = "New"
		elif flagged:
			movers += 1
			status = "Flagged"
		else:
			status = "OK"

		rows.append(
			{
				"proxy_sku": row.get("proxy_sku"),
				"pricing": pricing,
				"min_price": flt(row.get("min_price")),
				"max_price": flt(row.get("max_price")),
				"prev_min": flt(previous.get("min_price")) if previous else None,
				"prev_max": flt(previous.get("max_price")) if previous else None,
				"min_delta_pct": round(min_delta, 1),
				"max_delta_pct": round(max_delta, 1),
				"status": status,
			}
		)

	# SKUs that were in the previous snapshot but have vanished from the current set — the silent
	# coverage gap that hides at go-live when a whole strategy stops producing prices.
	dropped = [
		{"proxy_sku": previous.get("proxy_sku"), "pricing": pricing}
		for pricing, previous in previous_map.items()
		if pricing not in current_keys
	]

	return {
		"rows": rows,
		"summary": {
			"compared_against": previous_snapshot.name if previous_snapshot else None,
			"threshold_pct": threshold,
			"total": len(rows),
			"movers": movers,
			"new": new_count,
			"dropped": len(dropped),
		},
		"dropped": dropped,
	}


@frappe.whitelist()
def dry_run_pricing(refresh_first: bool = False) -> dict:
	frappe.only_for("System Manager")

	if cint(refresh_first):
		from navgold_custom.navgold_custom.amazon_pricing.strategy_selector import bulk_refresh_strategies

		bulk_refresh_strategies()

	settings = frappe.get_single("Amazon SP API Settings")

	# Fail loud on half-filled pre-go-live config: a feed missing seller/marketplace id would
	# silently differ from a real one rather than error.
	marketplace_id = settings.get_marketplace_id()
	if not settings.merchant_id or not marketplace_id:
		frappe.throw(
			_(
				"Amazon SP API Settings is missing Merchant ID or Marketplace — set both before running a dry run."
			)
		)

	pricing_rows = frappe.get_all(
		"Amazon Product Pricing",
		filters={"is_active": 1},
		fields=["name", "proxy_sku"],
	)
	allowed = get_allowed_pricing_proxy_skus()
	if allowed is not None:
		pricing_rows = [row for row in pricing_rows if row.proxy_sku in allowed]

	pricing_names = [row.name for row in pricing_rows]
	if not pricing_names:
		frappe.throw(_("No eligible pricing records to dry run."))

	feed_result = build_pricing_feed(pricing_names, settings.merchant_id, marketplace_id)
	json_content = feed_result["feed_content"]
	blocked_price_deviation = feed_result.get("blocked_price_deviation") or []
	blocked_validation = feed_result.get("blocked_validation") or []

	validation = validate_pricing_feed(
		json_content, require_pricing_patches=feed_result.get("publishing_mode") != "Stock Only"
	)

	snapshot = frappe.new_doc("Amazon Pricing Snapshot")
	snapshot.trigger_type = "Dry Run"
	snapshot.status = "Dry Run"
	snapshot.notes = "Dry run — feed rendered for review, not pushed to Amazon."
	snapshot.populate_from_current_pricing()
	variance = compute_price_variance(snapshot.get_rows())
	snapshot.variance_data = frappe.as_json(variance)
	snapshot.insert(ignore_permissions=True)

	log = frappe.new_doc("Amazon Pricing Submission Log")
	log.submission_type = "Amazon"
	log.status = "Dry Run"
	# pricing record names can be int (autoincrement naming), so coerce before joining.
	log.pricing_records = "\n".join(cstr(name) for name in pricing_names)
	log.feed_content = json_content
	log.insert(ignore_permissions=True)

	snapshot.db_set("submission_log", log.name)

	return {
		"snapshot": snapshot.name,
		"submission_log": log.name,
		"message_count": validation.get("message_count"),
		"valid": validation.get("valid"),
		"errors": validation.get("errors") or [],
		"blocked_price_deviation": len(blocked_price_deviation),
		"blocked_validation": len(blocked_validation),
		"variance": variance["summary"],
	}
