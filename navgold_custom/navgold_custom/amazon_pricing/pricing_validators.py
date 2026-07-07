# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.query_builder import DocType
from frappe.utils.data import flt

from navgold_custom.navgold_custom.amazon_pricing.price_calculator import (
	get_cost_sub_type_map,
	get_cost_type_map,
)
from navgold_custom.navgold_custom.amazon_pricing.price_guard import (
	partition_pricing_by_price_deviation,
)


def get_push_block_violations(strategy_names: list[str]) -> dict[str, list[str]]:
	# Per-strategy push blockers driven purely by the active cost rows. A row blocks if it is
	# (a) a percentage cost (Relative/VAT) sitting at 0 — percentages are never legitimately 0;
	# (b) a fed purchase cost (is_purchase_cost, sub-type source set) on Auto resolving to 0 — unsynced; or
	# (c) a defaulted cost (no sub-type source) on Auto sitting at 0 — the leaf default is missing/0.
	# FED absolute non-purchase zeros (e.g. FBA shipping resolving to 0) are allowed, as is any
	# Manual 0 — pinning a zero by hand is the seller's call.
	if not strategy_names:
		return {}

	cost_type_map = get_cost_type_map()
	cost_sub_type_map = get_cost_sub_type_map()

	cost_rows = frappe.get_all(
		"Amazon Product Cost",
		filters={
			"parenttype": "Amazon Product Strategy",
			"parent": ["in", strategy_names],
			"is_active": 1,
		},
		fields=["parent", "cost_type", "cost_sub_type", "value", "value_mode"],
		order_by="idx asc",
	)

	violations: dict[str, list[str]] = {}
	for cost in cost_rows:
		cost_type = cost_type_map.get(cost.cost_type)
		if not cost_type:
			continue

		value = flt(cost.value)
		sub_type_label = cost.cost_sub_type or cost.cost_type
		calculation_type = cost_type.calculation_type

		if calculation_type in ("Relative", "VAT") and value == 0:
			violations.setdefault(cost.parent, []).append(
				f"{sub_type_label}: zero percentage cost ({calculation_type})"
			)
			continue

		if cost.value_mode == "Auto" and value == 0:
			sub_type_source = (cost_sub_type_map.get(cost.cost_sub_type) or {}).get("source")
			if not sub_type_source:
				violations.setdefault(cost.parent, []).append(f"{sub_type_label}: defaulted cost at 0")
			elif cost_type.get("is_purchase_cost"):
				violations.setdefault(cost.parent, []).append(
					f"{sub_type_label}: unsynced purchase cost (source {sub_type_source})"
				)

	return violations


def get_purchase_cost_map(strategy_names: list[str]) -> dict[str, dict]:
	if not strategy_names:
		return {}

	cost = DocType("Amazon Product Cost")
	cost_type = DocType("Cost Type")
	rows = (
		frappe.qb.from_(cost)
		.join(cost_type)
		.on(cost.cost_type == cost_type.name)
		.select(cost.parent, cost.value, cost.value_mode)
		.where(
			(cost.parenttype == "Amazon Product Strategy")
			& (cost.parent.isin(strategy_names))
			& (cost.is_active == 1)
			& (cost.rank == 1)
			& (cost_type.is_purchase_cost == 1)
		)
	).run(as_dict=True)

	return {row.parent: {"value": flt(row.value), "value_mode": row.value_mode} for row in rows}


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
	push_block_map = get_push_block_violations(strategy_names)

	violations: dict[str, list[str]] = {}

	def add_violation(pricing_name: str, reason: str):
		violations.setdefault(pricing_name, []).append(reason)

	for pricing in pricing_rows:
		purchase_cost_value = flt(0)
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
					f"Purchase cost is {purchase_cost['value']:g} (value mode: {purchase_cost['value_mode']})",
				)
			else:
				purchase_cost_value = flt(purchase_cost["value"])

			for reason in push_block_map.get(pricing.active_strategy, []):
				add_violation(pricing.name, reason)

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
		# Hard money floor: never publish a min at or below purchase cost — this is the
		# exact below-cost leak that detection-only guards missed. Blocks just this SKU.
		if min_price > 0 and purchase_cost_value > 0 and min_price <= purchase_cost_value:
			add_violation(
				pricing.name,
				f"Min price {min_price:g} is at or below purchase cost {purchase_cost_value:g}",
			)

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
