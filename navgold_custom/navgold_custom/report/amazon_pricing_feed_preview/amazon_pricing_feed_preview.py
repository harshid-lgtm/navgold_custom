# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

"""Per-SKU preview of what the pricing sync would send to Amazon and Repricer. Read-only."""

import frappe
from frappe import _
from frappe.utils.csvutils import read_csv_content
from frappe.utils.data import cint, flt

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
	SHIPPING_GROUPS,
	build_pricing_feed,
	get_allowed_pricing_proxy_skus,
)
from navgold_custom.navgold_custom.amazon_pricing.price_guard import (
	partition_pricing_by_price_deviation,
)
from navgold_custom.navgold_custom.amazon_pricing.pricing_validators import (
	collect_pricing_violations,
)

AMAZON_COLUMNS = {
	"amazon_min",
	"amazon_max",
	"amazon_our_price",
	"quantity",
	"fulfillment",
	"shipping_group",
}
REPRICER_COLUMNS = {"repricer_min", "repricer_max", "repricer_rule"}

# Reverse of SHIPPING_GROUPS so the feed's merchant_shipping_group UUID shows as a readable label.
SHIPPING_GROUP_LABELS = {
	SHIPPING_GROUPS["sfp"]: "SFP",
	SHIPPING_GROUPS["main_revised"]: "Main Revised",
	SHIPPING_GROUPS["two_day"]: "Two-Day",
}


def execute(filters: dict | None = None):
	filters = filters or {}
	channel = filters.get("channel") or "Both"

	if (filters.get("source") or "Live Preview") == "Last Sent":
		rows, message = get_last_sent_rows(filters)
	else:
		rows, message = get_live_preview_rows(filters)

	status_filter = filters.get("status")
	if status_filter == "Blocked":
		rows = [row for row in rows if row["status"] == "Blocked"]
	elif status_filter == "Unblocked":
		rows = [row for row in rows if row["status"] != "Blocked"]

	return get_columns(channel), rows, message


def get_columns(channel: str) -> list[dict]:
	columns = [
		{
			"label": _("Proxy SKU"),
			"fieldname": "proxy_sku",
			"fieldtype": "Link",
			"options": "Proxy SKU",
			"width": 180,
		},
		{"label": _("Item"), "fieldname": "item", "fieldtype": "Link", "options": "Item", "width": 160},
		{
			"label": _("Active Strategy"),
			"fieldname": "active_strategy",
			"fieldtype": "Link",
			"options": "Amazon Product Strategy",
			"width": 200,
		},
		{"label": _("FBA"), "fieldname": "fba", "fieldtype": "Check", "width": 60},
		{"label": _("Publishing Mode"), "fieldname": "publishing_mode", "fieldtype": "Data", "width": 130},
		{"label": _("Amazon Min"), "fieldname": "amazon_min", "fieldtype": "Currency", "width": 110},
		{"label": _("Amazon Max"), "fieldname": "amazon_max", "fieldtype": "Currency", "width": 110},
		{
			"label": _("Amazon Our Price"),
			"fieldname": "amazon_our_price",
			"fieldtype": "Currency",
			"width": 120,
		},
		{"label": _("Qty"), "fieldname": "quantity", "fieldtype": "Int", "width": 70},
		{"label": _("Fulfillment"), "fieldname": "fulfillment", "fieldtype": "Data", "width": 200},
		{"label": _("Shipping Group"), "fieldname": "shipping_group", "fieldtype": "Data", "width": 130},
		{"label": _("Repricer Min"), "fieldname": "repricer_min", "fieldtype": "Currency", "width": 110},
		{"label": _("Repricer Max"), "fieldname": "repricer_max", "fieldtype": "Currency", "width": 110},
		{"label": _("Repricer Rule"), "fieldname": "repricer_rule", "fieldtype": "Data", "width": 160},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 90},
		{"label": _("Reason"), "fieldname": "reason", "fieldtype": "Data", "width": 360},
		{"label": _("Unblock"), "fieldname": "unblock", "fieldtype": "Data", "width": 100},
	]

	if channel == "Amazon":
		columns = [column for column in columns if column["fieldname"] not in REPRICER_COLUMNS]
	elif channel == "Repricer":
		columns = [column for column in columns if column["fieldname"] not in AMAZON_COLUMNS]

	return columns


def get_schedule_value(field: list | None) -> float | None:
	if not field:
		return None
	try:
		return flt(field[0]["schedule"][0]["value_with_tax"])
	except (KeyError, IndexError, TypeError):
		return None


def extract_amazon_values(message: dict) -> dict:
	values = {
		"amazon_min": None,
		"amazon_max": None,
		"amazon_our_price": None,
		"quantity": None,
		"fulfillment": None,
		"shipping_group": None,
	}
	for patch in message.get("patches", []):
		path = patch.get("path") or ""
		patch_value = patch.get("value") or []
		if path.endswith("merchant_shipping_group") and patch_value:
			group_uuid = patch_value[0].get("value")
			values["shipping_group"] = SHIPPING_GROUP_LABELS.get(group_uuid, group_uuid)
		elif path.endswith("purchasable_offer") and patch_value:
			offer = patch_value[0]
			values["amazon_max"] = get_schedule_value(offer.get("maximum_seller_allowed_price"))
			values["amazon_min"] = get_schedule_value(offer.get("minimum_seller_allowed_price"))
			values["amazon_our_price"] = get_schedule_value(offer.get("our_price"))
		elif path.endswith("fulfillment_availability") and patch_value:
			availability = patch_value[0]
			values["quantity"] = availability.get("quantity")
			parts = [availability.get("fulfillment_channel_code")]
			if availability.get("lead_time_to_ship_max_days") is not None:
				parts.append(f"lead {availability['lead_time_to_ship_max_days']}d")
			if availability.get("restock_date"):
				parts.append(f"restock {availability['restock_date']}")
			values["fulfillment"] = " · ".join(str(part) for part in parts if part)
	return values


def parse_feed_messages(feed_content: str | bytes) -> dict:
	if not feed_content:
		return {}
	feed = frappe.parse_json(feed_content)
	amazon_map = {}
	for message in feed.get("messages", []):
		proxy_sku = message.get("sku")
		if proxy_sku:
			amazon_map[proxy_sku] = extract_amazon_values(message)
	return amazon_map


def enrich_sku_metadata(proxy_sku_names: list[str]) -> dict:
	if not proxy_sku_names:
		return {}

	proxy_info = {
		row.name: {
			"item": row.item,
			"is_fba": row.is_fba,
			"current_selling_price": row.current_selling_price,
		}
		for row in frappe.get_all(
			"Proxy SKU",
			filters={"name": ["in", proxy_sku_names]},
			fields=["name", "item", "is_fba", "current_selling_price"],
		)
	}

	pricing_info = {
		row.proxy_sku: row.active_strategy
		for row in frappe.get_all(
			"Amazon Product Pricing",
			filters={"proxy_sku": ["in", proxy_sku_names]},
			fields=["proxy_sku", "active_strategy"],
		)
	}

	strategy_names = list({strategy for strategy in pricing_info.values() if strategy})
	repricer_rule_map = {}
	if strategy_names:
		repricer_rule_map = {
			row.name: row.repricer_rule
			for row in frappe.get_all(
				"Amazon Product Strategy",
				filters={"name": ["in", strategy_names]},
				fields=["name", "repricer_rule"],
			)
		}

	metadata = {}
	for proxy_sku in proxy_sku_names:
		active_strategy = pricing_info.get(proxy_sku)
		metadata[proxy_sku] = {
			"item": proxy_info.get(proxy_sku, {}).get("item"),
			"is_fba": cint(proxy_info.get(proxy_sku, {}).get("is_fba")),
			"current_selling_price": flt(proxy_info.get(proxy_sku, {}).get("current_selling_price")),
			"active_strategy": active_strategy,
			"repricer_rule": repricer_rule_map.get(active_strategy) or "",
		}
	return metadata


def build_preview_row(
	proxy_sku: str,
	meta: dict,
	amazon_values: dict,
	repricer: dict,
	*,
	active_strategy: str | None,
	publishing_mode: str,
	status: str,
	reason: str,
	can_unblock: int = 0,
	min_price: float | None = None,
	max_price: float | None = None,
	current_price: float | None = None,
) -> dict:
	return {
		"proxy_sku": proxy_sku,
		"item": meta.get("item"),
		"active_strategy": active_strategy,
		"fba": meta.get("is_fba", 0),
		"publishing_mode": publishing_mode,
		"amazon_min": amazon_values.get("amazon_min"),
		"amazon_max": amazon_values.get("amazon_max"),
		"amazon_our_price": amazon_values.get("amazon_our_price"),
		"quantity": amazon_values.get("quantity"),
		"fulfillment": amazon_values.get("fulfillment"),
		"shipping_group": amazon_values.get("shipping_group"),
		"repricer_min": repricer.get("repricer_min"),
		"repricer_max": repricer.get("repricer_max"),
		"repricer_rule": repricer.get("repricer_rule"),
		"status": status,
		"reason": reason,
		"can_unblock": can_unblock,
		"min_price": min_price,
		"max_price": max_price,
		"current_price": current_price,
	}


def get_live_preview_rows(filters: dict) -> tuple[list[dict], str | None]:
	settings = frappe.get_single("Amazon SP API Settings")
	marketplace_id = settings.get_marketplace_id()
	if not settings.merchant_id or not marketplace_id:
		frappe.throw(_("Amazon SP API Settings is missing Merchant ID or Marketplace."))

	if cint(filters.get("refresh_strategies")):
		from navgold_custom.navgold_custom.amazon_pricing.strategy_selector import bulk_refresh_strategies

		bulk_refresh_strategies()

	pricing_filters = {"is_active": 1}
	if filters.get("proxy_sku"):
		pricing_filters["proxy_sku"] = filters["proxy_sku"]

	pricing_records = frappe.get_all(
		"Amazon Product Pricing",
		filters=pricing_filters,
		fields=["name", "proxy_sku", "active_strategy", "current_min_price", "current_max_price"],
	)
	allowed = get_allowed_pricing_proxy_skus()
	message = None
	if allowed is not None:
		pricing_records = [row for row in pricing_records if row.proxy_sku in allowed]
		message = _(
			"Pricing publishing is restricted to an allow-list — only {0} SKU(s) would receive prices "
			"in a real push. Empty results here mean the allow-list is empty."
		).format(len(allowed))

	if not pricing_records:
		return [], message

	pricing_names = [row.name for row in pricing_records]
	publishing_mode = (
		frappe.get_cached_value("Navgold Settings", "Navgold Settings", "amazon_publishing_mode")
		or "Stock Only"
	)

	feed_result = build_pricing_feed(pricing_names, settings.merchant_id, marketplace_id)
	amazon_map = parse_feed_messages(feed_result.get("feed_content"))

	# Both channels share the same price guards.
	name_to_proxy = {row.name: row.proxy_sku for row in pricing_records}
	deviation_reasons = {}
	for record in partition_pricing_by_price_deviation(pricing_records)[1]:
		deviation_reasons[record["proxy_sku"]] = record["reason"]

	other_reasons = {}
	for pricing_name, reasons in collect_pricing_violations(pricing_names, include_deviation=False).items():
		other_reasons.setdefault(name_to_proxy.get(pricing_name, pricing_name), []).extend(reasons)

	blocked_reasons = {}
	for proxy_sku_name in set(deviation_reasons) | set(other_reasons):
		blocked_reasons[proxy_sku_name] = other_reasons.get(proxy_sku_name, []) + (
			[deviation_reasons[proxy_sku_name]] if proxy_sku_name in deviation_reasons else []
		)

	metadata = enrich_sku_metadata([row.proxy_sku for row in pricing_records])

	rows = []
	for pricing in pricing_records:
		proxy_sku = pricing.proxy_sku
		amazon_values = amazon_map.get(proxy_sku, {})
		meta = metadata.get(proxy_sku, {})
		is_blocked = proxy_sku in blocked_reasons

		repricer = {}
		if not is_blocked:
			# In stock-only mode the feed carries no price, so fall back to the current band.
			repricer = {
				"repricer_min": amazon_values.get("amazon_min")
				if amazon_values.get("amazon_min") is not None
				else flt(pricing.current_min_price),
				"repricer_max": amazon_values.get("amazon_max")
				if amazon_values.get("amazon_max") is not None
				else flt(pricing.current_max_price),
				"repricer_rule": meta.get("repricer_rule"),
			}

		rows.append(
			build_preview_row(
				proxy_sku,
				meta,
				amazon_values,
				repricer,
				active_strategy=pricing.active_strategy,
				publishing_mode=publishing_mode,
				status="Blocked" if is_blocked else "OK",
				reason="; ".join(blocked_reasons[proxy_sku]) if is_blocked else "",
				can_unblock=cint(proxy_sku in deviation_reasons and proxy_sku not in other_reasons),
				min_price=flt(pricing.current_min_price),
				max_price=flt(pricing.current_max_price),
				current_price=metadata.get(proxy_sku, {}).get("current_selling_price"),
			)
		)

	return rows, message


def get_latest_log(submission_type: str) -> dict | None:
	logs = frappe.get_all(
		"Amazon Pricing Submission Log",
		filters={
			"submission_type": submission_type,
			"status": ["!=", "Dry Run"],
			"feed_content": ["is", "set"],
		},
		fields=["name", "feed_content", "blocked_records", "submitted_at", "creation"],
		order_by="creation desc",
		limit=1,
	)
	return logs[0] if logs else None


def parse_blocked_records(blocked_records: str | None) -> dict:
	blocked = {}
	for line in (blocked_records or "").splitlines():
		if ":" not in line:
			continue
		proxy_sku, reason = line.split(":", 1)
		blocked[proxy_sku.strip()] = reason.strip()
	return blocked


def parse_repricer_csv(feed_content: str | None) -> dict:
	repricer_map = {}
	rows = read_csv_content(feed_content) if feed_content else []
	if not rows:
		return repricer_map
	header = rows[0]
	for values in rows[1:]:
		row = dict(zip(header, values, strict=False))
		sku = row.get("sku")
		if sku:
			repricer_map[sku] = {
				"repricer_min": flt(row.get("price_min")),
				"repricer_max": flt(row.get("price_max")),
				"repricer_rule": row.get("repricer_name") or "",
			}
	return repricer_map


def get_last_sent_rows(filters: dict) -> tuple[list[dict], str | None]:
	channel = filters.get("channel") or "Both"
	proxy_sku_filter = filters.get("proxy_sku")

	amazon_map = {}
	repricer_map = {}
	blocked_map = {}
	notes = []
	if channel in ("Both", "Amazon"):
		amazon_log = get_latest_log("Amazon")
		if amazon_log:
			amazon_map = parse_feed_messages(amazon_log.feed_content)
			blocked_map.update(parse_blocked_records(amazon_log.blocked_records))
			notes.append(_("Amazon: {0} ({1} SKUs)").format(amazon_log.name, len(amazon_map)))
		else:
			notes.append(_("Amazon: no completed push found"))

	if channel in ("Both", "Repricer"):
		repricer_log = get_latest_log("Repricer")
		if repricer_log:
			repricer_map = parse_repricer_csv(repricer_log.feed_content)
			blocked_map.update(parse_blocked_records(repricer_log.blocked_records))
			notes.append(_("Repricer: {0} ({1} SKUs)").format(repricer_log.name, len(repricer_map)))
		else:
			notes.append(_("Repricer: no completed push found"))

	message = _("Last sent — {0}").format(" · ".join(notes)) if notes else None

	proxy_sku_names = sorted(set(amazon_map) | set(repricer_map) | set(blocked_map))
	if proxy_sku_filter:
		proxy_sku_names = [name for name in proxy_sku_names if name == proxy_sku_filter]
	if not proxy_sku_names:
		return [], message

	metadata = enrich_sku_metadata(proxy_sku_names)

	rows = []
	for proxy_sku in proxy_sku_names:
		amazon_values = amazon_map.get(proxy_sku, {})
		repricer_values = repricer_map.get(proxy_sku, {})
		meta = metadata.get(proxy_sku, {})
		is_blocked = proxy_sku in blocked_map

		rows.append(
			build_preview_row(
				proxy_sku,
				meta,
				amazon_values,
				repricer_values,
				active_strategy=meta.get("active_strategy"),
				publishing_mode="",
				status="Blocked" if is_blocked else "Sent",
				reason=blocked_map.get(proxy_sku, ""),
			)
		)

	return rows, message
