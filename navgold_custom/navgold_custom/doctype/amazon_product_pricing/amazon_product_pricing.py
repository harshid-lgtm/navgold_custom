# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from navgold_custom.navgold_custom.amazon_pricing.strategy_selector import select_strategy


class AmazonProductPricing(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		active_strategy: DF.Link | None
		amazon_sync_status: DF.Literal["Pending", "Submitted", "Error"]
		backorder_strategy: DF.Link | None
		current_max_price: DF.Currency
		current_min_price: DF.Currency
		default_strategy: DF.Link | None
		fba_strategy: DF.Link | None
		is_active: DF.Check
		last_amazon_sync: DF.Datetime | None
		last_repricer_sync: DF.Datetime | None
		proxy_sku: DF.Link
		sfp_strategy: DF.Link | None
	# end: auto-generated types

	def validate(self):
		self.validate_strategies_belong_to_this()
		self.select_active_strategy()
		self.update_current_prices()

	def validate_strategies_belong_to_this(self):
		"""Ensure linked strategies belong to this pricing's Proxy SKU"""
		strategy_fields = ["fba_strategy", "sfp_strategy", "backorder_strategy", "default_strategy"]
		for field in strategy_fields:
			strategy = getattr(self, field)
			if strategy:
				strategy_doc = frappe.get_cached_doc("Amazon Product Strategy", strategy)
				if strategy_doc.proxy_sku != self.proxy_sku:
					frappe.throw(f"Strategy '{strategy}' does not belong to Proxy SKU '{self.proxy_sku}'")

	def select_active_strategy(self):
		"""Select the active strategy based on stock/fulfillment conditions"""

		self.active_strategy = select_strategy(self)

	def update_current_prices(self):
		"""Update current prices from active strategy"""
		if self.active_strategy:
			strategy = frappe.get_cached_doc("Amazon Product Strategy", self.active_strategy)
			self.current_min_price = strategy.calculated_min_price
			self.current_max_price = strategy.calculated_max_price
		else:
			self.current_min_price = 0
			self.current_max_price = 0

	@frappe.whitelist()
	def submit_to_amazon(self):
		"""Submit pricing to Amazon SP-API"""
		from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_submitter import submit_pricing

		if not self.is_active:
			frappe.throw("Cannot submit pricing for inactive products")

		if not self.current_min_price or not self.current_max_price:
			frappe.throw("Cannot submit pricing without calculated prices")

		result = submit_pricing([self.name])
		return result

	@frappe.whitelist()
	def sync_to_repricer(self):
		"""Sync pricing to Repricer.com"""
		from navgold_custom.navgold_custom.amazon_pricing.repricer_client import sync_to_repricer

		settings = frappe.get_single("Navgold Settings")
		if not settings.repricer_enabled:
			frappe.throw("Repricer integration is not enabled")

		if not self.is_active:
			frappe.throw("Cannot sync pricing for inactive products")

		result = sync_to_repricer([self.name])
		return result
