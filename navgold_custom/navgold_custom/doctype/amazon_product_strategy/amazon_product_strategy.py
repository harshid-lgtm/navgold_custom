# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils.data import flt


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
			filters=[
				["Amazon Product Pricing", "fba_strategy", "=", self.name],
				"or",
				["Amazon Product Pricing", "sfp_strategy", "=", self.name],
				"or",
				["Amazon Product Pricing", "backorder_strategy", "=", self.name],
				"or",
				["Amazon Product Pricing", "default_strategy", "=", self.name],
			],
		)
		for pricing in product_pricing:
			pricing = frappe.get_cached_doc("Amazon Product Pricing", pricing)
			pricing.update_current_prices()
			pricing.save()

	def validate_margin(self):
		"""Ensure min_margin <= max_margin"""
		if self.min_margin > self.max_margin:
			frappe.throw("Min Margin cannot be greater than Max Margin")

	def calculate_prices(self):
		"""Calculate min and max prices based on costs and margins"""
		from navgold_custom.navgold_custom.amazon_pricing.price_calculator import calculate_prices

		active_costs = [cost for cost in self.costs if cost.is_active]

		if not active_costs:
			self.calculated_min_price = 0
			self.calculated_max_price = 0
			return

		# Build cost data for calculator
		cost_data = []
		for cost in active_costs:
			cost_type = frappe.get_cached_doc("Cost Type", cost.cost_type)
			cost_data.append(
				{
					"type": cost.cost_type,
					"is_absolute": cost_type.is_absolute,
					"is_relative": cost_type.is_relative,
					"is_vat": cost_type.is_vat,
					"value": cost.value,
				}
			)

		prices = calculate_prices(
			costs=cost_data, min_margin=flt(self.min_margin) / 100, max_margin=flt(self.max_margin) / 100
		)

		self.calculated_min_price = prices.get("min_price", 0)
		self.calculated_max_price = prices.get("max_price", 0)

	def calculate_reverse_margin(self):
		"""Calculate margin from target price using reverse calculation"""
		from navgold_custom.navgold_custom.amazon_pricing.price_calculator import calculate_margin_from_price

		if not self.target_price:
			self.calculated_margin = 0
			self.calculated_profit = 0
			return

		active_costs = [cost for cost in self.costs if cost.is_active]

		if not active_costs:
			self.calculated_margin = 0
			self.calculated_profit = 0
			return

		# Build cost data for calculator
		cost_data = []
		for cost in active_costs:
			cost_type = frappe.get_cached_doc("Cost Type", cost.cost_type)
			cost_data.append(
				{
					"type": cost.cost_type,
					"is_absolute": cost_type.is_absolute,
					"is_relative": cost_type.is_relative,
					"is_vat": cost_type.is_vat,
					"value": cost.value,
				}
			)

		result = calculate_margin_from_price(costs=cost_data, selling_price=self.target_price)

		self.calculated_margin = result.get("margin", 0)
		self.calculated_profit = result.get("profit", 0)

	def populate_costs(self):
		for cost in self.costs:
			# The rank is redundant here will change it later
			cost.is_active = 1 if cost.rank == 1 else 0

			if cost.source == "Supplier-Sync":
				item = frappe.db.get_value("Proxy SKU", self.proxy_sku, "item")
				supplier_price = frappe.db.get_value(
					"Supplier Item Price and Stock",
					{"item": item, "purchase_blocked": 0, "stock_qty": [">", 0]},
					order_by="rank",
					fieldname="price",
				)
				cost.value = flt(supplier_price)

			if cost.source == "PO-Sync":
				item = frappe.db.get_value("Proxy SKU", self.proxy_sku, "item")
				po_cost = frappe.db.get_value(
					"Purchase Order Item",
					{"item_code": item, "docstatus": 1},
					order_by="creation desc",
					fieldname="rate",
				)
				cost.value = flt(po_cost)

			if cost.source == "FBA-Transfer":
				last_transfer_cost = frappe.db.get_value("Proxy SKU", self.proxy_sku, "last_transfer_rate")
				cost.value = flt(last_transfer_cost)
			if cost.source == "Auto-Sync":
				value = frappe.db.get_value("Cost Sub Type", cost.cost_sub_type, "default_value")
				cost.value = flt(value)
