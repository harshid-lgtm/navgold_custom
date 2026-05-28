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
)
from navgold_custom.navgold_custom.amazon_pricing.purchase_cost_sources import (
	get_effective_po_purchase_cost,
	get_effective_supplier_purchase_cost,
)
from navgold_custom.navgold_custom.utils import get_conversion_factor


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

		item_code = None
		proxy_sku_uom = None
		last_transfer_rate = None
		if synced_sources & {"Supplier-Sync", "PO-Sync", "FBA-Transfer"}:
			proxy_sku_row = frappe.db.get_value(
				"Proxy SKU",
				self.proxy_sku,
				["item", "uom", "last_transfer_rate"],
				as_dict=True,
			)
			if proxy_sku_row:
				item_code = proxy_sku_row.item
				proxy_sku_uom = proxy_sku_row.uom
				last_transfer_rate = proxy_sku_row.last_transfer_rate

		supplier_price = None
		if "Supplier-Sync" in synced_sources and item_code:
			supplier_price = get_effective_supplier_purchase_cost(item_code, proxy_sku_uom)

		po_cost = None
		if "PO-Sync" in synced_sources and item_code:
			po_cost = get_effective_po_purchase_cost(item_code, proxy_sku_uom)

		effective_transfer_rate = None
		if "FBA-Transfer" in synced_sources and item_code:
			effective_transfer_rate = flt(last_transfer_rate) * get_conversion_factor(
				item_code, proxy_sku_uom
			)

		sub_type_names = [
			cost.cost_sub_type for cost in self.costs if cost.source == "Auto-Sync" and cost.cost_sub_type
		]
		sub_type_defaults = {}
		if sub_type_names:
			sub_type_defaults = {
				row.name: row.default_value
				for row in frappe.get_all(
					"Cost Sub Type",
					filters={"name": ["in", sub_type_names]},
					fields=["name", "default_value"],
				)
			}

		for cost in self.costs:
			cost.is_active = 1 if cost.rank == 1 else 0

			if cost.source == "Supplier-Sync":
				cost.value = flt(supplier_price)
			elif cost.source == "PO-Sync":
				cost.value = flt(po_cost)
			elif cost.source == "FBA-Transfer":
				cost.value = flt(effective_transfer_rate)
			elif cost.source == "Auto-Sync":
				cost.value = flt(sub_type_defaults.get(cost.cost_sub_type))


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
	for strategy_name in strategy_names:
		try:
			strategy = frappe.get_doc("Amazon Product Strategy", strategy_name)
			strategy.save(ignore_permissions=True)
			refreshed += 1
		except Exception as exception:
			frappe.log_error(
				f"Daily strategy cost refresh failed for {strategy_name}: {exception!s}",
				"Amazon Product Strategy Refresh",
			)

	return {"total": len(strategy_names), "refreshed": refreshed}
