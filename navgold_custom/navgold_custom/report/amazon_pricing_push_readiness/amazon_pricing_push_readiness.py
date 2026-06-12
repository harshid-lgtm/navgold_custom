# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.data import cint

from navgold_custom.navgold_custom.amazon_pricing.pricing_validators import (
	collect_pricing_violations,
	get_purchase_cost_map,
)


def execute(filters: dict | None = None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns() -> list[dict]:
	return [
		{
			"label": _("Proxy SKU"),
			"fieldname": "proxy_sku",
			"fieldtype": "Link",
			"options": "Proxy SKU",
			"width": 180,
		},
		{
			"label": _("Item"),
			"fieldname": "item",
			"fieldtype": "Link",
			"options": "Item",
			"width": 180,
		},
		{
			"label": _("Active Strategy"),
			"fieldname": "active_strategy",
			"fieldtype": "Link",
			"options": "Amazon Product Strategy",
			"width": 220,
		},
		{
			"label": _("Min Price"),
			"fieldname": "min_price",
			"fieldtype": "Currency",
			"width": 110,
		},
		{
			"label": _("Max Price"),
			"fieldname": "max_price",
			"fieldtype": "Currency",
			"width": 110,
		},
		{
			"label": _("Purchase Cost"),
			"fieldname": "purchase_cost",
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"label": _("Cost Source"),
			"fieldname": "cost_source",
			"fieldtype": "Data",
			"width": 120,
		},
		{
			"label": _("Violations"),
			"fieldname": "violations",
			"fieldtype": "Data",
			"width": 420,
		},
	]


def get_data(filters: dict) -> list[dict]:
	pricing_rows = frappe.get_all(
		"Amazon Product Pricing",
		filters={"is_active": 1},
		fields=["name", "proxy_sku", "active_strategy", "current_min_price", "current_max_price"],
	)
	if not pricing_rows:
		return []

	violations_map = collect_pricing_violations([row.name for row in pricing_rows])

	proxy_sku_names = [row.proxy_sku for row in pricing_rows if row.proxy_sku]
	item_map = {}
	if proxy_sku_names:
		item_map = {
			row.name: row.item
			for row in frappe.get_all(
				"Proxy SKU",
				filters={"name": ["in", proxy_sku_names]},
				fields=["name", "item"],
			)
		}

	strategy_names = list({row.active_strategy for row in pricing_rows if row.active_strategy})
	purchase_cost_map = get_purchase_cost_map(strategy_names)
	strategy_name_map = {}
	if strategy_names:
		strategy_name_map = {
			row.name: row.strategy_name
			for row in frappe.get_all(
				"Amazon Product Strategy",
				filters={"name": ["in", strategy_names]},
				fields=["name", "strategy_name"],
			)
		}

	only_violations = cint(filters.get("only_violations"))
	strategy_name_filter = filters.get("strategy_name")

	data = []
	for pricing in pricing_rows:
		if strategy_name_filter and strategy_name_map.get(pricing.active_strategy) != strategy_name_filter:
			continue

		violations = violations_map.get(pricing.name, [])
		if only_violations and not violations:
			continue

		purchase_cost = purchase_cost_map.get(pricing.active_strategy) or {}
		data.append(
			{
				"proxy_sku": pricing.proxy_sku,
				"item": item_map.get(pricing.proxy_sku),
				"active_strategy": pricing.active_strategy,
				"min_price": pricing.current_min_price,
				"max_price": pricing.current_max_price,
				"purchase_cost": purchase_cost.get("value"),
				"cost_source": purchase_cost.get("source"),
				"violations": "; ".join(violations),
			}
		)

	return data
