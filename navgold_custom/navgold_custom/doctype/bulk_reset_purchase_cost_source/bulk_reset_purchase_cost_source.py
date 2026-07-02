# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import math

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count
from frappe.utils.data import flt

from navgold_custom.navgold_custom.amazon_pricing.price_calculator import VALUE_MODES

COST_UPDATE_CHUNK_SIZE = 500
STRATEGY_RECOMPUTE_CHUNK_SIZE = 500
RESET_JOB_TIMEOUT = 14400


class BulkResetPurchaseCostSource(Document):
	@frappe.whitelist()
	def apply_reset(self):
		if self.target_mode not in VALUE_MODES:
			frappe.throw(_("Target Mode must be Auto or Manual."))
		if not self.cost_type:
			frappe.throw(_("Cost Type is required."))
		if self.target_mode == "Manual" and self.manual_value in (None, ""):
			frappe.throw(_("Manual Value is required when Target Mode is Manual."))

		filters_payload = {
			"cost_type": self.cost_type,
			"strategy_name": self.strategy_name,
			"current_mode": self.current_mode,
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
			job_name=f"Bulk reset {self.cost_type} → {self.target_mode}",
			filters=filters_payload,
			target_mode=self.target_mode,
			manual_value=flt(self.manual_value) if self.target_mode == "Manual" else None,
			enqueued_by=frappe.session.user,
		)

		return {"matched_preview": matched_preview, "enqueued": True}


def reset_purchase_cost_sources(
	filters: dict, target_mode: str, manual_value: float | None, enqueued_by: str
):
	if target_mode not in VALUE_MODES:
		frappe.throw(_("Target Mode must be Auto or Manual."))
	# ponytail: bulk-reset to Auto leaves the FBA-fee value in place (Amazon-Sync is externally-owned,
	# populate_costs skips it), and the daily bulk_fetch_fba_fees refresh re-derives it. Re-wire an
	# immediate fetch only if a same-day reset must reflect the fee instantly.

	matching = get_matching_cost_rows(frappe._dict(filters))
	if not matching:
		frappe.log_error(
			f"Bulk reset job ran but found no rows. Filters: {filters}",
			"Bulk Reset Purchase Cost Source",
		)
		return

	update_fields = {"value_mode": target_mode}
	if target_mode == "Manual":
		update_fields["value"] = manual_value

	cost_names = [row["cost_name"] for row in matching]
	updated_count = 0
	for chunk_start in range(0, len(cost_names), COST_UPDATE_CHUNK_SIZE):
		chunk = cost_names[chunk_start : chunk_start + COST_UPDATE_CHUNK_SIZE]
		frappe.db.set_value(
			"Amazon Product Cost",
			{"name": ["in", chunk]},
			update_fields,
			update_modified=True,
		)
		# Persist each chunk in this long-running background job so progress survives.
		frappe.db.commit()  # nosemgrep
		updated_count += len(chunk)

	strategy_names = sorted({row["strategy"] for row in matching})
	chunks_enqueued = enqueue_strategy_recompute(strategy_names)

	frappe.publish_realtime(
		event="bulk_reset_purchase_cost_complete",
		message={
			"target_mode": target_mode,
			"matched": len(matching),
			"updated": updated_count,
			"enqueued_strategies": len(strategy_names),
			"chunks_enqueued": chunks_enqueued,
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
	if doc.current_mode:
		query = query.where(cost.value_mode == doc.current_mode)
	if doc.item:
		query = query.where(proxy_sku.item == doc.item)
	if doc.item_group:
		query = query.where(item.item_group == doc.item_group)
	if doc.proxy_sku:
		query = query.where(strategy.proxy_sku == doc.proxy_sku)
	return query


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
