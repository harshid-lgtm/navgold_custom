# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt, fmt_money

DROP_DIRECTIONS = {"Both", "Drops Only"}
RISE_DIRECTIONS = {"Both", "Increases Only"}


def get_pricing_alert_recipients() -> list[str]:
	raw = frappe.get_cached_value("Navgold Settings", "Navgold Settings", "pricing_alert_recipients") or ""
	recipients = [email.strip() for email in raw.replace("\n", ",").split(",") if email.strip()]

	if recipients:
		return recipients

	admin_email = frappe.db.get_value("User", "Administrator", "email")
	return [admin_email] if admin_email else []


def partition_pricing_by_price_deviation(pricing_records: list) -> tuple[list, list]:
	if not pricing_records:
		return [], []

	threshold = flt(
		frappe.get_cached_value("Navgold Settings", "Navgold Settings", "price_change_threshold_percent")
	)
	direction = (
		frappe.get_cached_value("Navgold Settings", "Navgold Settings", "price_change_block_direction")
		or "Both"
	)

	proxy_sku_names = list({record["proxy_sku"] for record in pricing_records if record.get("proxy_sku")})
	selling_price_map = {
		row.name: flt(row.current_selling_price)
		for row in frappe.get_all(
			"Proxy SKU",
			filters={"name": ["in", proxy_sku_names]},
			fields=["name", "current_selling_price"],
		)
	}

	allowed_records = []
	blocked_records = []

	for record in pricing_records:
		current = selling_price_map.get(record.get("proxy_sku"))
		new_min = flt(record.get("current_min_price"))
		new_max = flt(record.get("current_max_price"))

		if not current:
			blocked_records.append(
				{
					"proxy_sku": record.get("proxy_sku"),
					"current": current,
					"new_min": new_min,
					"new_max": new_max,
					"deviation_pct": 0.0,
					"reason": "No current selling price",
				}
			)
			continue

		# min/max is a band the repricer floats within; only a current price outside it forces a move.
		if current > new_max:
			deviation_pct = (current - new_max) / current * 100
			is_drop = True
		elif current < new_min:
			deviation_pct = (new_min - current) / current * 100
			is_drop = False
		else:
			deviation_pct = 0.0
			is_drop = False

		allowed_directions = DROP_DIRECTIONS if is_drop else RISE_DIRECTIONS

		if deviation_pct > threshold and direction in allowed_directions:
			verb = "drop" if is_drop else "rise"
			reason = f"Price {verb} {deviation_pct:.1f}% exceeds threshold {threshold:.1f}%"

			blocked_records.append(
				{
					"proxy_sku": record.get("proxy_sku"),
					"current": current,
					"new_min": new_min,
					"new_max": new_max,
					"deviation_pct": deviation_pct,
					"reason": reason,
				}
			)
		else:
			allowed_records.append(record)

	return allowed_records, blocked_records


def notify_price_deviation_blocks(blocked_records: list, channel_label: str):
	if not blocked_records:
		return

	recipients = get_pricing_alert_recipients()
	if not recipients:
		return

	def money(amount) -> str:
		return fmt_money(flt(amount), currency="GBP")

	rows_html = "".join(
		f"<tr>"
		f"<td style='padding:4px 8px;border:1px solid #ddd'>{record['proxy_sku']}</td>"
		f"<td style='padding:4px 8px;border:1px solid #ddd'>{money(record['current'])}</td>"
		f"<td style='padding:4px 8px;border:1px solid #ddd'>{money(record['new_min'])}</td>"
		f"<td style='padding:4px 8px;border:1px solid #ddd'>{money(record['new_max'])}</td>"
		f"<td style='padding:4px 8px;border:1px solid #ddd'>{record['deviation_pct']:.1f}%</td>"
		f"<td style='padding:4px 8px;border:1px solid #ddd'>{record['reason']}</td>"
		f"</tr>"
		for record in blocked_records
	)

	message = (
		f"<p>{len(blocked_records)} SKU(s) were blocked from {channel_label} pricing sync "
		f"by the price-deviation guard.</p>"
		"<table style='border-collapse:collapse'>"
		"<thead><tr>"
		"<th style='padding:4px 8px;border:1px solid #ddd'>SKU</th>"
		"<th style='padding:4px 8px;border:1px solid #ddd'>Current Price</th>"
		"<th style='padding:4px 8px;border:1px solid #ddd'>New Min</th>"
		"<th style='padding:4px 8px;border:1px solid #ddd'>New Max</th>"
		"<th style='padding:4px 8px;border:1px solid #ddd'>Deviation</th>"
		"<th style='padding:4px 8px;border:1px solid #ddd'>Reason</th>"
		"</tr></thead>"
		f"<tbody>{rows_html}</tbody></table>"
	)

	frappe.sendmail(
		recipients=recipients,
		subject=f"[Navgold] {len(blocked_records)} SKUs blocked from {channel_label} pricing sync",
		message=message,
	)
