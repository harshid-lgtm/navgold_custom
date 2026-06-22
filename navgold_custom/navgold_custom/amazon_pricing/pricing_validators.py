# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.query_builder import DocType
from frappe.utils.data import flt

from navgold_custom.navgold_custom.amazon_pricing.price_guard import (
	partition_pricing_by_price_deviation,
)


def get_purchase_cost_map(strategy_names: list[str]) -> dict[str, dict]:
	if not strategy_names:
		return {}

	cost = DocType("Amazon Product Cost")
	cost_type = DocType("Cost Type")
	rows = (
		frappe.qb.from_(cost)
		.join(cost_type)
		.on(cost.cost_type == cost_type.name)
		.select(cost.parent, cost.value, cost.source)
		.where(
			(cost.parenttype == "Amazon Product Strategy")
			& (cost.parent.isin(strategy_names))
			& (cost.is_active == 1)
			& (cost.rank == 1)
			& (cost_type.is_purchase_cost == 1)
		)
	).run(as_dict=True)

	return {row.parent: {"value": flt(row.value), "source": row.source} for row in rows}


def collect_pricing_violations(
	pricing_names: list[str], include_deviation: bool = True
) -> dict[str, list[str]]:
	if not pricing_names:
		return {}

	pricing_rows = frappe.get_all(
		"Amazon Product Pricing",
		filters={"name": ["in", pricing_names]},
		fields=[
			"name",
			"proxy_sku",
			"active_strategy",
			"fixed_price",
			"current_min_price",
			"current_max_price",
		],
	)

	strategy_names = list({row.active_strategy for row in pricing_rows if row.active_strategy})
	strategy_status_map = {}
	if strategy_names:
		strategy_status_map = {
			row.name: row.is_active
			for row in frappe.get_all(
				"Amazon Product Strategy",
				filters={"name": ["in", strategy_names]},
				fields=["name", "is_active"],
			)
		}
	purchase_cost_map = get_purchase_cost_map(strategy_names)

	violations: dict[str, list[str]] = {}

	def add_violation(pricing_name: str, reason: str):
		violations.setdefault(pricing_name, []).append(reason)

	for pricing in pricing_rows:
		if not pricing.active_strategy:
			add_violation(pricing.name, "No active strategy")
		elif pricing.active_strategy not in strategy_status_map:
			add_violation(pricing.name, f"Active strategy {pricing.active_strategy} does not exist")
		elif not strategy_status_map[pricing.active_strategy]:
			add_violation(pricing.name, f"Active strategy {pricing.active_strategy} is disabled")
		else:
			purchase_cost = purchase_cost_map.get(pricing.active_strategy)
			if purchase_cost is None:
				add_violation(pricing.name, "No active purchase cost row on the active strategy")
			elif purchase_cost["value"] <= 0:
				add_violation(
					pricing.name,
					f"Purchase cost is {purchase_cost['value']:g} (source: {purchase_cost['source']})",
				)

		min_price = flt(pricing.current_min_price)
		max_price = flt(pricing.current_max_price)
		if min_price <= 0:
			add_violation(pricing.name, f"Min price is {min_price:g}")
		if max_price <= 0:
			add_violation(pricing.name, f"Max price is {max_price:g}")
		if min_price > 0 and max_price > 0:
			if min_price > max_price:
				add_violation(pricing.name, f"Min price {min_price:g} is above max price {max_price:g}")
			elif min_price == max_price and not pricing.fixed_price:
				add_violation(pricing.name, "Min price equals max price without a fixed price")

	if include_deviation:
		pricing_name_by_proxy_sku = {row.proxy_sku: row.name for row in pricing_rows if row.proxy_sku}
		deviation_blocks = partition_pricing_by_price_deviation(pricing_rows)[1]
		for block in deviation_blocks:
			pricing_name = pricing_name_by_proxy_sku.get(block["proxy_sku"])
			if pricing_name:
				add_violation(pricing_name, block["reason"])

	return violations


def merge_blocked_reasons(validation_blocks: list, deviation_blocks: list) -> dict[str, list[str]]:
	blocked_reasons: dict[str, list[str]] = {}
	for block in validation_blocks:
		blocked_reasons.setdefault(block["proxy_sku"], []).extend(block["reasons"])
	for block in deviation_blocks:
		blocked_reasons.setdefault(block["proxy_sku"], []).append(block["reason"])
	return blocked_reasons


def format_blocked_records(blocked_reasons: dict[str, list[str]]) -> str:
	return "\n".join(
		f"{proxy_sku}: {'; '.join(reasons)}" for proxy_sku, reasons in sorted(blocked_reasons.items())
	)
