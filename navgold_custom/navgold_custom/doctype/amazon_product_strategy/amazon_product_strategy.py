# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.data import flt

from navgold_custom.navgold_custom.amazon_pricing.price_calculator import (
	calculate_margin_from_price,
	calculate_prices,
	get_cost_data,
	get_item_tax_rate,
)
from navgold_custom.navgold_custom.amazon_pricing.purchase_cost_sources import (
	get_effective_po_purchase_cost,
	get_effective_supplier_purchase_cost,
	log_missing_bundle_component_rates,
)
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


ITEM_SYNCED_SOURCE_RESOLVERS = {
	"Supplier-Sync": resolve_supplier_sync,
	"PO-Sync": resolve_po_sync,
	"FBA-Transfer": resolve_fba_transfer,
	"Item-Tax-Sync": resolve_item_tax_sync,
}


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
		self.validate_margin()
		self.populate_costs()
		self.calculate_prices()
		self.calculate_reverse_margin()

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

	def calculate_prices(self):
		cost_data = get_cost_data(self)

		if not cost_data:
			self.calculated_min_price = 0
			self.calculated_max_price = 0
			return

		prices = calculate_prices(
			costs=cost_data, min_margin=flt(self.min_margin) / 100, max_margin=flt(self.max_margin) / 100
		)

		self.calculated_min_price = prices.get("min_price", 0)
		self.calculated_max_price = prices.get("max_price", 0)

	def calculate_reverse_margin(self):
		if not self.target_price:
			self.calculated_margin = 0
			self.calculated_profit = 0
			return

		cost_data = get_cost_data(self)

		if not cost_data:
			self.calculated_margin = 0
			self.calculated_profit = 0
			return

		result = calculate_margin_from_price(costs=cost_data, selling_price=self.target_price)

		self.calculated_margin = result.get("margin", 0)
		self.calculated_profit = result.get("profit", 0)

	def populate_costs(self):
		synced_sources = {cost.source for cost in self.costs if cost.source not in (None, "", "Manual")}

		synced_values = self.resolve_synced_source_values(synced_sources)
		sub_type_defaults = self.get_auto_sync_defaults()

		for cost in self.costs:
			cost.is_active = 1 if cost.rank == 1 else 0

			if cost.source in synced_values:
				cost.value = flt(synced_values[cost.source])
			elif cost.source == "Auto-Sync":
				cost.value = flt(sub_type_defaults.get(cost.cost_sub_type))

	def resolve_synced_source_values(self, synced_sources) -> dict:
		active_resolvers = {
			source: resolver
			for source, resolver in ITEM_SYNCED_SOURCE_RESOLVERS.items()
			if source in synced_sources
		}
		if not active_resolvers:
			return {}

		proxy_sku_row = frappe.db.get_value(
			"Proxy SKU", self.proxy_sku, ["item", "uom", "last_transfer_rate"], as_dict=True
		)
		if not proxy_sku_row or not proxy_sku_row.item:
			return dict.fromkeys(active_resolvers, 0)

		context = {
			"item_code": proxy_sku_row.item,
			"uom": proxy_sku_row.uom,
			"last_transfer_rate": proxy_sku_row.last_transfer_rate,
			# Set by refresh_strategy_costs_chunk so misses across a whole run land in one Error Log
			"missing_component_rates": self.flags.get("missing_bundle_component_rates"),
		}
		return {source: resolver(context) for source, resolver in active_resolvers.items()}

	def get_auto_sync_defaults(self) -> dict:
		sub_type_names = [
			cost.cost_sub_type for cost in self.costs if cost.source == "Auto-Sync" and cost.cost_sub_type
		]
		if not sub_type_names:
			return {}
		return {
			row.name: row.default_value
			for row in frappe.get_all(
				"Cost Sub Type",
				filters={"name": ["in", sub_type_names]},
				fields=["name", "default_value"],
			)
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
