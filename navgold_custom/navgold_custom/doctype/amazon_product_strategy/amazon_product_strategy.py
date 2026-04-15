# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.data import flt

from navgold_custom.navgold_custom.amazon_pricing.price_calculator import (
	calculate_margin_from_price,
	calculate_prices,
)
from navgold_custom.navgold_custom.amazon_pricing.price_calculator_api import get_cost_data_for_strategy


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
		product_pricing = frappe.get_all(
			"Amazon Product Pricing",
			or_filters={
				"fba_strategy": self.name,
				"sfp_strategy": self.name,
				"backorder_strategy": self.name,
				"default_strategy": self.name,
			},
			pluck="name",
		)
		for pricing_name in product_pricing:
			pricing = frappe.get_cached_doc("Amazon Product Pricing", pricing_name)
			pricing.update_current_prices()
			pricing.save(ignore_permissions=True)

	def validate_margin(self):
		if self.min_margin > self.max_margin:
			frappe.throw(_("Min Margin cannot be greater than Max Margin"))

	def calculate_prices(self):
		cost_data = get_cost_data_for_strategy(self)

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

		cost_data = get_cost_data_for_strategy(self)

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
		if synced_sources & {"Supplier-Sync", "PO-Sync"}:
			item_code = frappe.db.get_value("Proxy SKU", self.proxy_sku, "item")

		last_transfer_rate = None
		if "FBA-Transfer" in synced_sources:
			last_transfer_rate = frappe.db.get_value("Proxy SKU", self.proxy_sku, "last_transfer_rate")

		supplier_price = None
		if "Supplier-Sync" in synced_sources and item_code:
			supplier_price = frappe.db.get_value(
				"Supplier Item Price and Stock",
				{"item": item_code, "purchase_blocked": 0, "stock_qty": [">", 0]},
				order_by="rank",
				fieldname="price",
			)

		po_cost = None
		if "PO-Sync" in synced_sources and item_code:
			po_cost = frappe.db.get_value(
				"Purchase Order Item",
				{"item_code": item_code, "docstatus": 1},
				order_by="creation desc",
				fieldname="rate",
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
				cost.value = flt(last_transfer_rate)
			elif cost.source == "Auto-Sync":
				cost.value = flt(sub_type_defaults.get(cost.cost_sub_type))
