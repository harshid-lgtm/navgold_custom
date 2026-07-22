# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.data import flt

from navgold_custom.navgold_custom.amazon_pricing.fba_fee_fetcher import FEE_SOURCE
from navgold_custom.navgold_custom.amazon_pricing.price_calculator import (
	calculate_margin_from_price,
	calculate_prices,
	get_cost_data,
	get_cost_sub_type_map,
	get_item_tax_rate,
)
from navgold_custom.navgold_custom.amazon_pricing.purchase_cost_sources import (
	get_effective_po_purchase_cost,
	get_effective_supplier_purchase_cost,
	log_missing_bundle_component_rates,
)
from navgold_custom.navgold_custom.amazon_pricing.shipping_cost import get_shipping_cost
from navgold_custom.navgold_custom.utils import get_conversion_factor


# Add a sync provider by registering a resolver below; populate_costs() needs no change. Manual
# (user value) and Amazon-Sync (owned by the FBA fee fetcher) are deliberately not resolved here.
def resolve_supplier_sync(context):
	return get_effective_supplier_purchase_cost(
		context["item_code"], context["uom"], context["missing_component_rates"]
	)


def resolve_po_sync(context):
	return get_effective_po_purchase_cost(
		context["item_code"], context["uom"], context["missing_component_rates"]
	)


def resolve_fba_transfer(context):
	return flt(context["last_transfer_rate"]) * get_conversion_factor(context["item_code"], context["uom"])


def resolve_item_tax_sync(context):
	return get_item_tax_rate(context["item_code"])


def resolve_shipping(context):
	# Amazon ships FBA orders, so the FBA strategy carries no merchant shipping cost.
	if context["is_fba_strategy"]:
		return 0
	return get_shipping_cost(context["shipping_weight_g"])


# The single registry of every source a Cost Type leaf can carry. A callable resolves the value on
# save; None marks an externally-owned source (the FBA fee fetcher owns Amazon-Sync) whose row value
# is left exactly as the owning process wrote it. A source missing here is a misconfiguration.
SOURCE_RESOLVERS = {
	"Supplier-Sync": resolve_supplier_sync,
	"PO-Sync": resolve_po_sync,
	"FBA-Transfer": resolve_fba_transfer,
	"Item-Tax-Sync": resolve_item_tax_sync,
	"Shipping-Sync": resolve_shipping,
	FEE_SOURCE: None,
}


def is_externally_owned(source) -> bool:
	return source in SOURCE_RESOLVERS and SOURCE_RESOLVERS[source] is None


class AmazonProductStrategy(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from navgold_custom.navgold_custom.doctype.amazon_product_cost.amazon_product_cost import (
			AmazonProductCost,
		)

		calculated_max_price: DF.Currency
		calculated_min_price: DF.Currency
		costs: DF.Table[AmazonProductCost]
		is_active: DF.Check
		max_margin: DF.Percent
		min_margin: DF.Percent
		proxy_sku: DF.Link
		repricer_rule: DF.Link | None
		strategy_name: DF.Data
	# end: auto-generated types

	def validate(self):
		cost_sub_type_map = get_cost_sub_type_map()
		self.validate_margin()
		# Must run BEFORE populate_costs: populate_costs rewrites Auto rows from the feed/default on every
		# save, so a pin run afterwards would see the feed's own write as a change and pin every row. Run
		# here and cost.value still holds the externally-written value vs the last-saved one — only a
		# genuine external edit (REST, db.set_value, import, script) differs and gets pinned to Manual.
		self.pin_external_value_overrides(cost_sub_type_map)
		self.populate_costs(cost_sub_type_map)
		self.calculate_prices(cost_sub_type_map)
		self.calculate_reverse_margin(cost_sub_type_map)

	def pin_external_value_overrides(self, cost_sub_type_map: dict):
		before = self.get_doc_before_save()
		if before is None:
			return

		precision = self.precision("value", "costs")

		before_rows = {row.name: row for row in before.costs}

		for cost in self.costs:
			if cost.value_mode == "Manual":
				continue

			cost_source = (cost_sub_type_map.get(cost.cost_sub_type) or {}).get("source")
			if is_externally_owned(cost_source):
				# Written by the owning process (FBA fee fetcher keeps value_mode=Auto) — a value change
				# here is not a human override, so it must not be pinned.
				continue

			previous_row = before_rows.get(cost.name)
			if previous_row is None:
				continue

			# A deliberate Manual→Auto switch zeroes the value in the grid; that drop is a mode change,
			# not an external override, so hand the row to populate_costs instead of pinning it back.
			if previous_row.value_mode == "Manual":
				continue

			if flt(cost.value, precision) != flt(previous_row.value, precision):
				cost.value_mode = "Manual"

	def on_update(self):
		pricing = frappe.db.get_value(
			"Amazon Product Pricing",
			self.proxy_sku,
			["active_strategy", "fixed_price"],
			as_dict=True,
		)

		if not pricing or pricing.fixed_price or pricing.active_strategy != self.name:
			return
		frappe.db.set_value(
			"Amazon Product Pricing",
			self.proxy_sku,
			{
				"current_min_price": self.calculated_min_price,
				"current_max_price": self.calculated_max_price,
			},
			update_modified=False,
		)

	def validate_margin(self):
		if self.min_margin > self.max_margin:
			frappe.throw(_("Min Margin cannot be greater than Max Margin"))

	def calculate_prices(self, cost_sub_type_map: dict):
		cost_data = get_cost_data(self, cost_sub_type_map=cost_sub_type_map)

		if not cost_data:
			self.calculated_min_price = 0
			self.calculated_max_price = 0
			return

		prices = calculate_prices(
			costs=cost_data, min_margin=flt(self.min_margin) / 100, max_margin=flt(self.max_margin) / 100
		)

		self.calculated_min_price = prices.get("min_price", 0)
		self.calculated_max_price = prices.get("max_price", 0)

	def calculate_reverse_margin(self, cost_sub_type_map: dict):
		if not self.target_price:
			self.calculated_margin = 0
			self.calculated_profit = 0
			return

		cost_data = get_cost_data(self, cost_sub_type_map=cost_sub_type_map)

		if not cost_data:
			self.calculated_margin = 0
			self.calculated_profit = 0
			return

		result = calculate_margin_from_price(costs=cost_data, selling_price=self.target_price)

		self.calculated_margin = result.get("margin", 0)
		self.calculated_profit = result.get("profit", 0)

	def populate_costs(self, cost_sub_type_map: dict):
		# The sub-type's source (not the row) decides resolution; the row's value_mode only gates stickiness.
		# Externally-owned sources are left to their owning process, so they never enter the resolver dispatch.
		fed_sub_types = {
			cost.cost_sub_type
			for cost in self.costs
			if cost.value_mode != "Manual"
			and SOURCE_RESOLVERS.get(cost_sub_type_map.get(cost.cost_sub_type, {}).get("source")) is not None
		}
		fed_values = self.resolve_fed_sub_type_values(fed_sub_types, cost_sub_type_map)

		for cost in self.costs:
			cost.is_active = 1 if cost.rank == 1 else 0

			if cost.value_mode == "Manual":
				continue

			sub_type_config = cost_sub_type_map.get(cost.cost_sub_type)
			cost_source = sub_type_config.get("source") if sub_type_config else None

			if not cost_source:
				cost.value = flt(sub_type_config.get("default_value")) if sub_type_config else 0
			elif cost_source not in SOURCE_RESOLVERS:
				# A source with no registry entry is a misconfiguration. Don't throw (that would abort
				# a whole bulk refresh run); log once and leave the value untouched.
				frappe.log_error(
					f"Cost row {cost.cost_sub_type} has unregistered source {cost_source!r}",
					"Amazon Product Strategy Cost Source",
				)
				continue
			elif is_externally_owned(cost_source):
				# Owned by another process (e.g. the FBA fee fetcher) — leave the value untouched.
				continue
			else:
				resolved = fed_values.get(cost.cost_sub_type)
				# Resolver returned None → feed not ready; leave at 0 (unsynced) rather than a stale value.
				cost.value = flt(resolved) if resolved is not None else 0

	def resolve_fed_sub_type_values(self, fed_sub_types, cost_sub_type_map: dict) -> dict:
		# fed_sub_types already excludes Manual rows and externally-owned/unresolved sources.
		active_sources = {cost_sub_type_map[sub_type]["source"] for sub_type in fed_sub_types}
		if not active_sources:
			return {}

		proxy_sku_row = frappe.db.get_value(
			"Proxy SKU",
			self.proxy_sku,
			["item", "uom", "last_transfer_rate", "shipping_weight_g"],
			as_dict=True,
		)
		if not proxy_sku_row or not proxy_sku_row.item:
			return dict.fromkeys(fed_sub_types, 0)

		context = {
			"item_code": proxy_sku_row.item,
			"uom": proxy_sku_row.uom,
			"last_transfer_rate": proxy_sku_row.last_transfer_rate,
			"shipping_weight_g": proxy_sku_row.shipping_weight_g,
			# Set by refresh_strategy_costs_chunk so misses across a whole run land in one Error Log
			"missing_component_rates": self.flags.get("missing_bundle_component_rates"),
		}

		if "Shipping-Sync" in active_sources:
			fba_strategy = frappe.db.get_value("Amazon Product Pricing", self.proxy_sku, "fba_strategy")
			context["is_fba_strategy"] = fba_strategy == self.name

		resolved_by_source = {source: SOURCE_RESOLVERS[source](context) for source in active_sources}
		return {
			sub_type: resolved_by_source.get(cost_sub_type_map[sub_type]["source"])
			for sub_type in fed_sub_types
		}


STRATEGY_REFRESH_CHUNK_SIZE = 5000


@frappe.whitelist()
def refresh_all_strategy_costs():
	strategy_names = frappe.get_all(
		"Amazon Product Strategy",
		filters={"is_active": 1},
		pluck="name",
	)

	chunks = [
		strategy_names[index : index + STRATEGY_REFRESH_CHUNK_SIZE]
		for index in range(0, len(strategy_names), STRATEGY_REFRESH_CHUNK_SIZE)
	]

	for chunk in chunks:
		frappe.enqueue(
			"navgold_custom.navgold_custom.doctype.amazon_product_strategy.amazon_product_strategy.refresh_strategy_costs_chunk",
			queue="long",
			timeout=6000,
			strategy_names=chunk,
		)

	return {"total": len(strategy_names), "chunks_enqueued": len(chunks)}


def refresh_strategy_costs_chunk(strategy_names: list):
	refreshed = 0
	missing_component_rates: list[tuple[str, str]] = []
	for strategy_name in strategy_names:
		try:
			strategy = frappe.get_doc("Amazon Product Strategy", strategy_name)
			strategy.flags.missing_bundle_component_rates = missing_component_rates
			strategy.save(ignore_permissions=True)
			refreshed += 1
		except Exception as exception:
			frappe.log_error(
				f"Daily strategy cost refresh failed for {strategy_name}: {exception!s}",
				"Amazon Product Strategy Refresh",
			)

	log_missing_bundle_component_rates(missing_component_rates)

	return {"total": len(strategy_names), "refreshed": refreshed}
