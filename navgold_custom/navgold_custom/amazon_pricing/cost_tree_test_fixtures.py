# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

# Shared test builders for the Cost Type tree suites. ensure_* helpers are idempotent reference-data
# setup; create_/build_ helpers mint per-test docs. Every insert rides the caller's IntegrationTestCase
# transaction — nothing here commits, so rollback semantics stay with the test.

from unittest.mock import patch

import frappe

from navgold_custom.navgold_custom.amazon_pricing import price_calculator


def ensure_item(item_code, **overrides):
	if frappe.db.exists("Item", item_code):
		return item_code
	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = item_code
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 0
	item.update(overrides)
	item.insert()
	return item.name


def ensure_cost_group(name, calculation_type="Absolute", is_purchase_cost=0, allowed_zero_valued=0):
	if frappe.db.exists("Cost Type", name):
		return name
	group = frappe.new_doc("Cost Type")
	group.cost_type_name = name
	group.is_group = 1
	group.calculation_type = calculation_type
	group.is_purchase_cost = is_purchase_cost
	group.allowed_zero_valued = allowed_zero_valued
	group.insert()
	return group.name


def ensure_cost_leaf(parent, leaf_name, source="", default_value=0, allowed_zero_valued=0):
	full_name = f"{parent}-{leaf_name}"
	if frappe.db.exists("Cost Type", full_name):
		return full_name
	leaf = frappe.new_doc("Cost Type")
	leaf.cost_type_name = leaf_name
	leaf.is_group = 0
	leaf.parent_cost_type = parent
	leaf.source = source
	leaf.default_value = default_value
	leaf.allowed_zero_valued = allowed_zero_valued
	leaf.insert()
	return full_name


def create_proxy_sku(item=None, uom=None, is_active=1, last_transfer_rate=None, selling_price=None):
	# asin left unset so Proxy SKU.validate's duplicate-active check never trips.
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.is_active = is_active
	if item:
		proxy_sku.item = item
	if uom:
		proxy_sku.uom = uom
	if last_transfer_rate is not None:
		proxy_sku.last_transfer_rate = last_transfer_rate
	if selling_price is not None:
		proxy_sku.current_selling_price = selling_price
	proxy_sku.insert()
	return proxy_sku.name


def build_strategy(proxy_sku, rows, strategy_name="default", min_margin=5, max_margin=10, insert=True):
	strategy = frappe.new_doc("Amazon Product Strategy")
	strategy.proxy_sku = proxy_sku
	strategy.strategy_name = strategy_name
	strategy.is_active = 1
	strategy.min_margin = min_margin
	strategy.max_margin = max_margin
	for row in rows:
		strategy.append("costs", row)
	if insert:
		strategy.insert()
	return strategy


def insert_with_fixed_settings(strategy, settings):
	with patch.object(price_calculator, "get_pricing_settings", return_value=settings):
		strategy.insert()
	return strategy
