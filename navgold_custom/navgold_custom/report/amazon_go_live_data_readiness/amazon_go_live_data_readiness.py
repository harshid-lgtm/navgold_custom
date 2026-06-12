# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Max
from frappe.utils.data import cint, flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{
			"label": _("Proxy SKU"),
			"fieldname": "proxy_sku",
			"fieldtype": "Link",
			"options": "Proxy SKU",
			"width": 160,
		},
		{"label": _("Item"), "fieldname": "item", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("Is FBA"), "fieldname": "is_fba", "fieldtype": "Check", "width": 70},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 90},
		{
			"label": _("Active Strategy"),
			"fieldname": "active_strategy",
			"fieldtype": "Link",
			"options": "Amazon Product Strategy",
			"width": 180,
		},
		{"label": _("Purchase Cost"), "fieldname": "purchase_cost", "fieldtype": "Currency", "width": 110},
		{"label": _("Min Price"), "fieldname": "min_price", "fieldtype": "Currency", "width": 100},
		{"label": _("Max Price"), "fieldname": "max_price", "fieldtype": "Currency", "width": 100},
		{"label": _("Issues"), "fieldname": "issues", "fieldtype": "Data", "width": 420},
	]


def get_data(filters):
	proxies = frappe.get_all(
		"Proxy SKU",
		filters={
			"is_active": 1,
			**({"is_fba": cint(filters.is_fba)} if filters.get("is_fba") not in (None, "") else {}),
		},
		fields=["name", "item", "is_fba", "asin"],
		limit=0,
	)
	if not proxies:
		return []

	proxy_names = [proxy.name for proxy in proxies]
	pricing_map = {
		row.proxy_sku: row
		for row in frappe.get_all(
			"Amazon Product Pricing",
			filters={"proxy_sku": ["in", proxy_names]},
			fields=[
				"name",
				"proxy_sku",
				"is_active",
				"active_strategy",
				"current_min_price",
				"current_max_price",
			],
			limit=0,
		)
	}

	strategy_names = [row.active_strategy for row in pricing_map.values() if row.active_strategy]
	strategy_map = {
		row.name: row
		for row in frappe.get_all(
			"Amazon Product Strategy",
			filters={"name": ["in", strategy_names]},
			fields=["name", "is_active", "min_margin", "max_margin"],
			limit=0,
		)
	}
	purchase_cost_map = get_purchase_cost_map(strategy_names)

	rows = []
	for proxy in proxies:
		row = build_readiness_row(proxy, pricing_map.get(proxy.name), strategy_map, purchase_cost_map)
		rows.append(row)

	if cint(filters.get("only_issues", 1)):
		rows = [row for row in rows if row["status"] == "BLOCKED"]
	return rows


def get_purchase_cost_map(strategy_names):
	if not strategy_names:
		return {}

	cost = DocType("Amazon Product Cost")
	cost_type = DocType("Cost Type")
	rows = (
		frappe.qb.from_(cost)
		.join(cost_type)
		.on(cost.cost_type == cost_type.name)
		.select(cost.parent, Max(cost.value).as_("purchase_cost"))
		.where((cost.parent.isin(strategy_names)) & (cost.is_active == 1) & (cost_type.is_purchase_cost == 1))
		.groupby(cost.parent)
	).run(as_dict=True)
	return {row.parent: flt(row.purchase_cost) for row in rows}


def build_readiness_row(proxy, pricing, strategy_map, purchase_cost_map):
	issues = []
	strategy = None
	purchase_cost = 0.0

	if not proxy.asin:
		issues.append(_("no ASIN"))

	if not pricing:
		issues.append(_("no Amazon Product Pricing"))
	else:
		if not pricing.is_active:
			issues.append(_("pricing inactive"))
		if not pricing.active_strategy:
			issues.append(_("no active strategy"))
		else:
			strategy = strategy_map.get(pricing.active_strategy)
			if not strategy:
				issues.append(_("active strategy missing"))
			else:
				if not strategy.is_active:
					issues.append(_("active strategy disabled"))
				if not flt(strategy.min_margin) and not flt(strategy.max_margin):
					issues.append(_("margins not set"))
				purchase_cost = purchase_cost_map.get(strategy.name, 0.0)
				if purchase_cost <= 0:
					issues.append(_("purchase cost missing or 0"))

		min_price = flt(pricing.current_min_price)
		max_price = flt(pricing.current_max_price)
		if min_price <= 0 or max_price <= 0:
			issues.append(_("min/max price not computed"))
		elif min_price > max_price:
			issues.append(_("min price above max price"))

	return {
		"proxy_sku": proxy.name,
		"item": proxy.item,
		"is_fba": proxy.is_fba,
		"status": "BLOCKED" if issues else "READY",
		"active_strategy": pricing.active_strategy if pricing else None,
		"purchase_cost": purchase_cost,
		"min_price": flt(pricing.current_min_price) if pricing else 0,
		"max_price": flt(pricing.current_max_price) if pricing else 0,
		"issues": "; ".join(issues),
	}
