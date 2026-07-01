# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.data import flt

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_submitter import submit_pricing
from navgold_custom.navgold_custom.amazon_pricing.price_guard import (
	partition_pricing_by_price_deviation,
)
from navgold_custom.navgold_custom.amazon_pricing.pricing_validators import collect_pricing_violations
from navgold_custom.navgold_custom.amazon_pricing.repricer_client import sync_to_repricer
from navgold_custom.navgold_custom.amazon_pricing.strategy_selector import select_strategy


class AmazonProductPricing(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		active_strategy: DF.Link | None
		amazon_sync_status: DF.Literal["Pending", "Submitted", "Error", "Skipped"]
		backorder_strategy: DF.Link | None
		current_max_price: DF.Currency
		current_min_price: DF.Currency
		default_strategy: DF.Link | None
		fba_strategy: DF.Link | None
		fixed_price: DF.Check
		fixed_price_value: DF.Currency
		is_active: DF.Check
		item: DF.Link | None
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
		strategy_fields = ["fba_strategy", "sfp_strategy", "backorder_strategy", "default_strategy"]
		for field in strategy_fields:
			strategy = getattr(self, field)
			if strategy:
				strategy_doc = frappe.get_cached_doc("Amazon Product Strategy", strategy)
				if strategy_doc.proxy_sku != self.proxy_sku:
					frappe.throw(
						_("Strategy '{0}' does not belong to Proxy SKU '{1}'").format(
							strategy, self.proxy_sku
						)
					)

	def select_active_strategy(self):
		self.active_strategy = select_strategy(self)

	def update_current_prices(self):
		if self.fixed_price:
			self.current_min_price = flt(self.fixed_price_value)
			self.current_max_price = flt(self.fixed_price_value)
			return

		if self.active_strategy:
			strategy = frappe.get_cached_doc("Amazon Product Strategy", self.active_strategy)
			self.current_min_price = strategy.calculated_min_price
			self.current_max_price = strategy.calculated_max_price
		else:
			self.current_min_price = 0
			self.current_max_price = 0

	def guard_price_deviation(self):
		record = {
			"name": self.name,
			"proxy_sku": self.proxy_sku,
			"current_min_price": self.current_min_price,
			"current_max_price": self.current_max_price,
		}
		blocked = partition_pricing_by_price_deviation([record])[1]
		if blocked:
			frappe.throw(
				_("Pricing for {0} is blocked by the price-deviation guard: {1}").format(
					self.proxy_sku, blocked[0]["reason"]
				)
			)

	@frappe.whitelist()
	def submit_to_amazon(self):
		if not frappe.db.get_single_value("Navgold Settings", "enable_pricing_sync"):
			frappe.throw(_("Pricing sync is disabled in Navgold Settings"))

		if not self.is_active:
			frappe.throw(_("Cannot submit pricing for inactive products"))

		publishing_mode = (
			frappe.get_cached_value("Navgold Settings", "Navgold Settings", "amazon_publishing_mode")
			or "Stock Only"
		)
		# Stock Only feeds carry no price patches, so price preconditions must not block them
		if publishing_mode == "Stock and Prices":
			if not self.current_min_price or not self.current_max_price:
				frappe.throw(_("Cannot submit pricing without calculated prices"))

			self.guard_price_deviation()

			violations = collect_pricing_violations([self.name], include_deviation=False)
			if violations.get(self.name):
				frappe.throw(
					_("Pricing for {0} fails validation: {1}").format(
						self.proxy_sku, "; ".join(violations[self.name])
					)
				)

		return submit_pricing([self.name])

	@frappe.whitelist()
	def sync_to_repricer(self):
		settings = frappe.get_single("Navgold Settings")
		if not settings.repricer_enabled:
			frappe.throw(_("Repricer integration is not enabled"))

		if not self.is_active:
			frappe.throw(_("Cannot sync pricing for inactive products"))

		self.guard_price_deviation()

		return sync_to_repricer([self.name])
