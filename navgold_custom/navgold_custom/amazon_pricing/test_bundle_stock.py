# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import get_sku_inventory_data
from navgold_custom.navgold_custom.amazon_pricing.strategy_selector import (
	get_stock_info,
	get_stock_info_batch,
	select_strategy,
)
from navgold_custom.navgold_custom.amazon_pricing.template_applicator import apply_template_to_proxy_sku
from navgold_custom.navgold_custom.amazon_pricing.test_template_applicator import create_strategy_template


def make_item(item_code, is_stock_item):
	if frappe.db.exists("Item", item_code):
		return item_code
	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = item_code
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = is_stock_item
	item.insert()
	return item.name


def get_stock_warehouse():
	# A real (non-rejected, in-stock) warehouse so get_free_qty_map counts the Bin we build.
	company = (
		frappe.defaults.get_global_default("company") or frappe.get_all("Company", limit=1, pluck="name")[0]
	)
	warehouse = frappe.get_all(
		"Warehouse",
		filters={"company": company, "is_group": 0, "is_rejected_warehouse": 0, "disabled": 0},
		limit=1,
		pluck="name",
	)
	if not warehouse:
		raise frappe.ValidationError("No usable warehouse on test site")
	return warehouse[0], company


def receive_stock(item_code, qty, warehouse, company):
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.stock_entry_type = "Material Receipt"
	stock_entry.company = company
	stock_entry.append(
		"items",
		{
			"item_code": item_code,
			"qty": qty,
			"t_warehouse": warehouse,
			"basic_rate": 10,
		},
	)
	stock_entry.insert()
	stock_entry.submit()
	return stock_entry.name


def make_bundle(bundle_item_code, component_specs):
	# component_specs: list of (component_item_code, qty_per_bundle)
	if frappe.db.exists("Product Bundle", {"new_item_code": bundle_item_code}):
		return
	bundle = frappe.new_doc("Product Bundle")
	bundle.new_item_code = bundle_item_code
	bundle.is_active = 1
	for component_item_code, qty in component_specs:
		bundle.append(
			"items",
			{"item_code": component_item_code, "qty": qty, "uom": "Nos", "description": component_item_code},
		)
	bundle.insert()
	# Product Bundle is submittable on this version; get_bundle_components only counts submitted bundles.
	if bundle.meta.is_submittable and bundle.docstatus == 0:
		bundle.submit()


def make_proxy_sku(item_code, template_name):
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.item = item_code
	proxy_sku.uom = "Nos"
	proxy_sku.is_active = 1
	proxy_sku.insert()
	apply_template_to_proxy_sku(template_name, proxy_sku.name)
	return proxy_sku.name


class TestBundleStockFromComponents(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.template_name = create_strategy_template()
		cls.warehouse, cls.company = get_stock_warehouse()

		suffix = frappe.generate_hash(length=6)

		# Buildable bundle: parent non-stock, both components stocked.
		# component_a = 15, component_b = 1500, qty per bundle 1 each -> buildable = min(15, 1500) = 15.
		cls.component_a = make_item(f"TEST-BUNDLE-COMP-A-{suffix}", is_stock_item=1)
		cls.component_b = make_item(f"TEST-BUNDLE-COMP-B-{suffix}", is_stock_item=1)
		receive_stock(cls.component_a, 15, cls.warehouse, cls.company)
		receive_stock(cls.component_b, 1500, cls.warehouse, cls.company)
		cls.bundle_item = make_item(f"TEST-BUNDLE-PARENT-{suffix}", is_stock_item=0)
		make_bundle(cls.bundle_item, [(cls.component_a, 1), (cls.component_b, 1)])
		cls.bundle_buildable = 15
		cls.bundle_proxy_sku = make_proxy_sku(cls.bundle_item, cls.template_name)

		# Out-of-stock bundle: one component at 0 -> buildable 0.
		cls.oos_component_full = make_item(f"TEST-BUNDLE-OOS-FULL-{suffix}", is_stock_item=1)
		cls.oos_component_empty = make_item(f"TEST-BUNDLE-OOS-EMPTY-{suffix}", is_stock_item=1)
		receive_stock(cls.oos_component_full, 50, cls.warehouse, cls.company)
		cls.oos_bundle_item = make_item(f"TEST-BUNDLE-OOS-PARENT-{suffix}", is_stock_item=0)
		make_bundle(cls.oos_bundle_item, [(cls.oos_component_full, 1), (cls.oos_component_empty, 1)])
		cls.oos_bundle_proxy_sku = make_proxy_sku(cls.oos_bundle_item, cls.template_name)

		# Non-bundle control: plain stocked item, free_qty = 42.
		cls.plain_item = make_item(f"TEST-BUNDLE-PLAIN-{suffix}", is_stock_item=1)
		cls.plain_qty = 42
		receive_stock(cls.plain_item, cls.plain_qty, cls.warehouse, cls.company)
		cls.plain_proxy_sku = make_proxy_sku(cls.plain_item, cls.template_name)

	def test_get_stock_info_bundle_uses_buildable_as_mfn(self):
		stock_info = get_stock_info(self.bundle_proxy_sku)
		self.assertEqual(stock_info["mfn_stock"], self.bundle_buildable)
		self.assertEqual(stock_info["total_stock"], self.bundle_buildable)

	def test_get_stock_info_non_bundle_uses_free_qty(self):
		stock_info = get_stock_info(self.plain_proxy_sku)
		self.assertEqual(stock_info["mfn_stock"], self.plain_qty)

	def test_get_stock_info_batch_matches_single(self):
		batch = get_stock_info_batch([self.bundle_proxy_sku, self.plain_proxy_sku, self.oos_bundle_proxy_sku])
		self.assertEqual(batch[self.bundle_proxy_sku]["mfn_stock"], self.bundle_buildable)
		self.assertEqual(batch[self.plain_proxy_sku]["mfn_stock"], self.plain_qty)
		self.assertEqual(batch[self.oos_bundle_proxy_sku]["mfn_stock"], 0)

	def test_buildable_bundle_selects_non_backorder_and_publishes_qty(self):
		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": self.bundle_proxy_sku})
		selected = select_strategy(pricing)
		# Both components in stock and no FBA -> SFP (some MFN stock), never Backorder.
		self.assertEqual(selected, pricing.sfp_strategy)
		self.assertNotEqual(selected, pricing.backorder_strategy)

		pricing.active_strategy = selected
		inventory = get_sku_inventory_data(pricing)
		# pool_size = 1 (single proxy sku), pack_size = 1 (Nos) -> quantity == buildable.
		self.assertEqual(inventory["quantity"], self.bundle_buildable)
		self.assertGreater(inventory["quantity"], 0)

	def test_out_of_stock_bundle_zero_qty_and_backorder(self):
		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": self.oos_bundle_proxy_sku})
		selected = select_strategy(pricing)
		self.assertEqual(selected, pricing.backorder_strategy)

		pricing.active_strategy = selected
		inventory = get_sku_inventory_data(pricing)
		self.assertEqual(inventory["quantity"], 0)

	def test_non_bundle_publishes_free_qty_unchanged(self):
		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": self.plain_proxy_sku})
		selected = select_strategy(pricing)
		self.assertEqual(selected, pricing.sfp_strategy)

		pricing.active_strategy = selected
		inventory = get_sku_inventory_data(pricing)
		self.assertEqual(inventory["quantity"], self.plain_qty)
