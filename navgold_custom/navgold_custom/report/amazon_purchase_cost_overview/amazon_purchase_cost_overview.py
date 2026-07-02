# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.utils.data import flt

from navgold_custom.navgold_custom.amazon_pricing.price_calculator import (
	VALUE_MODES,
	get_cost_sub_type_map,
)
from navgold_custom.navgold_custom.amazon_pricing.purchase_cost_sources import (
	get_effective_po_purchase_costs,
	get_effective_supplier_purchase_costs,
)
from navgold_custom.navgold_custom.utils import get_conversion_factor


def execute(filters: dict | None = None):
	filters = filters or {}
	columns = get_columns()
	data = get_data(filters)
	return columns, data


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
			"label": _("Strategy"),
			"fieldname": "strategy",
			"fieldtype": "Link",
			"options": "Amazon Product Strategy",
			"width": 260,
		},
		{
			"label": _("Strategy Name"),
			"fieldname": "strategy_name",
			"fieldtype": "Data",
			"width": 110,
		},
		{
			"label": _("Mode"),
			"fieldname": "current_mode",
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"label": _("Feed Source"),
			"fieldname": "feed_source",
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"label": _("Current Value"),
			"fieldname": "current_value",
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"label": _("Supplier-Sync"),
			"fieldname": "supplier_sync_preview",
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"label": _("PO-Sync"),
			"fieldname": "po_sync_preview",
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"label": _("FBA-Transfer"),
			"fieldname": "fba_transfer_preview",
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"label": _("Last Updated"),
			"fieldname": "last_updated",
			"fieldtype": "Datetime",
			"width": 160,
		},
	]


def get_data(filters: dict) -> list[dict]:
	purchase_cost_types = frappe.get_all("Cost Type", filters={"is_purchase_cost": 1}, pluck="name")
	if not purchase_cost_types:
		return []

	rows = fetch_active_purchase_cost_rows(filters, purchase_cost_types)
	if not rows:
		return []

	item_uom_pairs = list({(row["item"], row["uom"]) for row in rows if row.get("item") and row.get("uom")})

	supplier_map = get_effective_supplier_purchase_costs(item_uom_pairs)
	po_map = get_effective_po_purchase_costs(item_uom_pairs)
	policy = get_cost_sub_type_map()

	for row in rows:
		key = (row.get("item"), row.get("uom"))
		row["feed_source"] = (policy.get(row.get("cost_sub_type")) or {}).get("source")
		row["supplier_sync_preview"] = supplier_map.get(key)
		row["po_sync_preview"] = po_map.get(key)
		row["fba_transfer_preview"] = (
			flt(row.get("last_transfer_rate")) * get_conversion_factor(row["item"], row["uom"])
			if row.get("is_fba") and row.get("item") and row.get("uom")
			else None
		)

	source_type = filters.get("source_type") or "All"
	if source_type == "Manual":
		rows = [row for row in rows if row["current_mode"] == "Manual"]
	elif source_type == "Auto":
		rows = [row for row in rows if row["current_mode"] != "Manual"]

	return rows


def fetch_active_purchase_cost_rows(filters: dict, purchase_cost_types: list[str]) -> list[dict]:
	cost = DocType("Amazon Product Cost")
	strategy = DocType("Amazon Product Strategy")
	proxy_sku = DocType("Proxy SKU")
	item = DocType("Item")

	query = (
		frappe.qb.from_(cost)
		.join(strategy)
		.on(cost.parent == strategy.name)
		.join(proxy_sku)
		.on(strategy.proxy_sku == proxy_sku.name)
		.join(item)
		.on(item.name == proxy_sku.item)
		.select(
			proxy_sku.name.as_("proxy_sku"),
			proxy_sku.item.as_("item"),
			proxy_sku.uom.as_("uom"),
			proxy_sku.is_fba.as_("is_fba"),
			proxy_sku.last_transfer_rate.as_("last_transfer_rate"),
			strategy.name.as_("strategy"),
			strategy.strategy_name.as_("strategy_name"),
			cost.cost_sub_type.as_("cost_sub_type"),
			cost.value_mode.as_("current_mode"),
			cost.value.as_("current_value"),
			cost.modified.as_("last_updated"),
		)
		.where(
			(cost.parenttype == "Amazon Product Strategy")
			& (cost.rank == 1)
			& (cost.cost_type.isin(purchase_cost_types))
		)
	)

	if filters.get("strategy_name"):
		query = query.where(strategy.strategy_name == filters["strategy_name"])
	if filters.get("proxy_sku"):
		query = query.where(strategy.proxy_sku == filters["proxy_sku"])
	if filters.get("item"):
		query = query.where(proxy_sku.item == filters["item"])
	if filters.get("item_group"):
		query = query.where(item.item_group == filters["item_group"])

	return query.run(as_dict=True)


@frappe.whitelist()
def bulk_update_purchase_cost_source(rows: str):
	rows = frappe.parse_json(rows) or []
	if not rows:
		frappe.throw(_("No rows provided."))

	purchase_cost_types = set(frappe.get_all("Cost Type", filters={"is_purchase_cost": 1}, pluck="name"))

	updated = 0
	skipped = []

	for row in rows:
		strategy_name = row.get("strategy")
		value_mode = row.get("source")
		value = row.get("value")

		if value_mode not in VALUE_MODES:
			skipped.append({"strategy": strategy_name, "reason": _("Value Mode must be Auto or Manual")})
			continue

		if value_mode == "Manual" and (value in (None, "")):
			skipped.append({"strategy": strategy_name, "reason": _("Manual requires a value")})
			continue

		strategy = frappe.get_doc("Amazon Product Strategy", strategy_name)
		purchase_cost_row = find_active_purchase_cost_row(strategy, purchase_cost_types)
		if not purchase_cost_row:
			skipped.append({"strategy": strategy_name, "reason": _("No active purchase cost row")})
			continue

		purchase_cost_row.value_mode = value_mode
		if value_mode == "Manual":
			purchase_cost_row.value = flt(value)

		strategy.save()
		updated += 1

	return {"updated": updated, "skipped": skipped}


def find_active_purchase_cost_row(strategy, purchase_cost_types):
	for cost_row in strategy.costs:
		if cost_row.cost_type in purchase_cost_types and cost_row.rank == 1:
			return cost_row
	return None
