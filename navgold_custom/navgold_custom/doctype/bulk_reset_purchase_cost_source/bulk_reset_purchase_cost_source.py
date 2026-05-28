# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count
from frappe.utils.data import flt

COST_UPDATE_CHUNK_SIZE = 500
STRATEGY_RECOMPUTE_CHUNK_SIZE = 500
RESET_JOB_TIMEOUT = 14400

EXPLICIT_TARGET_SOURCES = {"Manual", "Supplier-Sync", "PO-Sync", "FBA-Transfer"}
ALL_TARGET_SOURCES = EXPLICIT_TARGET_SOURCES | {"Auto"}

SUB_TYPE_TO_AUTO_SOURCE = {
	"Purchase Cost-Warehouse Cost": "PO-Sync",
	"Purchase Cost-Top Supplier": "Supplier-Sync",
	"Purchase Cost-Last Transfer": "FBA-Transfer",
	"Fulfillment Cost-Estimated FBA Fee": "Amazon-Sync",
}
DEFAULT_AUTO_SOURCE = "Auto-Sync"


class BulkResetPurchaseCostSource(Document):
	@frappe.whitelist()
	def apply_reset(self):
		if self.target_source not in ALL_TARGET_SOURCES:
			frappe.throw(_("Invalid Target Source."))
		if not self.cost_type:
			frappe.throw(_("Cost Type is required."))
		if self.target_source == "Manual" and self.manual_value in (None, ""):
			frappe.throw(_("Manual Value is required when Target Source is Manual."))

		filters_payload = {
			"cost_type": self.cost_type,
			"strategy_name": self.strategy_name,
			"current_source": self.current_source,
			"item": self.item,
			"item_group": self.item_group,
			"proxy_sku": self.proxy_sku,
		}

		matched_preview = count_matching_cost_rows(filters_payload)
		if not matched_preview:
			frappe.throw(_("No matching cost rows for the given filters."))

		frappe.enqueue(
			"navgold_custom.navgold_custom.doctype.bulk_reset_purchase_cost_source.bulk_reset_purchase_cost_source.reset_purchase_cost_sources",
			queue="long",
			timeout=RESET_JOB_TIMEOUT,
			job_name=f"Bulk reset {self.cost_type} → {self.target_source}",
			filters=filters_payload,
			target_source=self.target_source,
			manual_value=flt(self.manual_value) if self.target_source == "Manual" else None,
			enqueued_by=frappe.session.user,
		)

		return {"matched_preview": matched_preview, "enqueued": True}


def reset_purchase_cost_sources(
	filters: dict, target_source: str, manual_value: float | None, enqueued_by: str
):
	matching = get_matching_cost_rows(frappe._dict(filters))
	if not matching:
		frappe.log_error(
			f"Bulk reset job ran but found no rows. Filters: {filters}",
			"Bulk Reset Purchase Cost Source",
		)
		return

	updates_by_target = group_updates_by_target(matching, target_source)

	updated_count = 0
	for target, cost_names in updates_by_target.items():
		update_fields = {"source": target}
		if target == "Manual":
			update_fields["value"] = manual_value
		for chunk_start in range(0, len(cost_names), COST_UPDATE_CHUNK_SIZE):
			chunk = cost_names[chunk_start : chunk_start + COST_UPDATE_CHUNK_SIZE]
			frappe.db.set_value(
				"Amazon Product Cost",
				{"name": ["in", chunk]},
				update_fields,
				update_modified=True,
			)
			# Persist each chunk in this long-running background job so progress survives. # nosemgrep
			frappe.db.commit()
			updated_count += len(chunk)

	strategy_names = sorted({row["strategy"] for row in matching})
	chunks_enqueued = enqueue_strategy_recompute(strategy_names)

	frappe.publish_realtime(
		event="bulk_reset_purchase_cost_complete",
		message={
			"target_source": target_source,
			"matched": len(matching),
			"updated": updated_count,
			"enqueued_strategies": len(strategy_names),
			"chunks_enqueued": chunks_enqueued,
			"target_breakdown": {target: len(names) for target, names in updates_by_target.items()},
		},
		user=enqueued_by,
	)


def count_matching_cost_rows(filters: dict) -> int:
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
		.select(Count(cost.name).as_("total"))
		.where((cost.parenttype == "Amazon Product Strategy") & (cost.cost_type == filters["cost_type"]))
	)
	query = apply_optional_filters(query, frappe._dict(filters), cost, strategy, proxy_sku, item)
	result = query.run(as_dict=True)
	return int(result[0]["total"]) if result else 0


def get_matching_cost_rows(doc) -> list[dict]:
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
			cost.name.as_("cost_name"),
			cost.cost_sub_type.as_("cost_sub_type"),
			strategy.name.as_("strategy"),
			strategy.strategy_name.as_("strategy_name"),
			proxy_sku.name.as_("proxy_sku"),
		)
		.where((cost.parenttype == "Amazon Product Strategy") & (cost.cost_type == doc.cost_type))
	)
	query = apply_optional_filters(query, doc, cost, strategy, proxy_sku, item)
	return query.run(as_dict=True)


def apply_optional_filters(query, doc, cost, strategy, proxy_sku, item):
	if doc.strategy_name:
		query = query.where(strategy.strategy_name == doc.strategy_name)
	if doc.current_source:
		query = query.where(cost.source == doc.current_source)
	if doc.item:
		query = query.where(proxy_sku.item == doc.item)
	if doc.item_group:
		query = query.where(item.item_group == doc.item_group)
	if doc.proxy_sku:
		query = query.where(strategy.proxy_sku == doc.proxy_sku)
	return query


def group_updates_by_target(rows: list[dict], target_source: str) -> dict[str, list[str]]:
	if target_source != "Auto":
		return {target_source: [row["cost_name"] for row in rows]}

	buckets: dict[str, list[str]] = defaultdict(list)
	for row in rows:
		mapped_source = SUB_TYPE_TO_AUTO_SOURCE.get(row["cost_sub_type"], DEFAULT_AUTO_SOURCE)
		buckets[mapped_source].append(row["cost_name"])
	return dict(buckets)


def enqueue_strategy_recompute(strategy_names: list[str]) -> int:
	if not strategy_names:
		return 0

	chunks = [
		strategy_names[index : index + STRATEGY_RECOMPUTE_CHUNK_SIZE]
		for index in range(0, len(strategy_names), STRATEGY_RECOMPUTE_CHUNK_SIZE)
	]

	for chunk in chunks:
		frappe.enqueue(
			"navgold_custom.navgold_custom.doctype.amazon_product_strategy.amazon_product_strategy.refresh_strategy_costs_chunk",
			queue="long",
			timeout=6000,
			strategy_names=chunk,
		)

	return math.ceil(len(strategy_names) / STRATEGY_RECOMPUTE_CHUNK_SIZE)
