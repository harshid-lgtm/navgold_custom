# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.shipping_weight import (
	get_shipping_weight_g,
	item_weight_in_grams,
)

PACK6_UOM = "Test-Shipping-Pack-6"


def ensure_uom(name):
	if not frappe.db.exists("UOM", name):
		uom = frappe.new_doc("UOM")
		uom.uom_name = name
		uom.insert()
	return name


def create_item(item_code, weight_per_unit=0, weight_uom=None, pack_factor=None):
	if frappe.db.exists("Item", item_code):
		return item_code
	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = item_code
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 0
	item.weight_per_unit = weight_per_unit
	item.weight_uom = weight_uom
	if pack_factor:
		ensure_uom(PACK6_UOM)
		item.append("uoms", {"uom": PACK6_UOM, "conversion_factor": pack_factor})
	item.insert()
	return item.name


def create_bundle(bundle_item_code, components):
	# components: list of (item_code, qty, uom)
	create_item(bundle_item_code)
	bundle = frappe.new_doc("Product Bundle")
	bundle.new_item_code = bundle_item_code
	for component_item, qty, uom in components:
		bundle.append("items", {"item_code": component_item, "qty": qty, "uom": uom})
	bundle.insert()
	if bundle.meta.is_submittable:
		bundle.submit()
	return bundle_item_code


class TestShippingWeight(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_item("TEST-SW-GRAM", weight_per_unit=300, weight_uom="Gram")
		create_item("TEST-SW-KG", weight_per_unit=1.5, weight_uom="Kg")
		create_item("TEST-SW-BAD-UOM", weight_per_unit=450, weight_uom="Nos")
		create_item("TEST-SW-NOWEIGHT")
		create_item("TEST-SW-PACK", weight_per_unit=250, weight_uom="Gram", pack_factor=6)

	def test_item_weight_in_grams_gram(self):
		self.assertEqual(item_weight_in_grams("TEST-SW-GRAM"), 300)

	def test_item_weight_in_grams_kg_converts(self):
		self.assertEqual(item_weight_in_grams("TEST-SW-KG"), 1500)

	def test_item_weight_in_grams_unusable_uom_is_zero(self):
		self.assertEqual(item_weight_in_grams("TEST-SW-BAD-UOM"), 0)
		self.assertEqual(item_weight_in_grams("TEST-SW-NOWEIGHT"), 0)

	def test_single_item_nos(self):
		self.assertEqual(get_shipping_weight_g("TEST-SW-GRAM", "Nos"), 300)

	def test_pack_uom_multiplies_by_conversion_factor(self):
		# 250g per unit, pack of 6 -> 1500g
		self.assertEqual(get_shipping_weight_g("TEST-SW-PACK", PACK6_UOM), 1500)

	def test_bundle_sums_component_weights_by_qty(self):
		create_item("TEST-SW-COMP-A", weight_per_unit=300, weight_uom="Gram")
		create_item("TEST-SW-COMP-B", weight_per_unit=500, weight_uom="Gram")
		create_bundle(
			"TEST-SW-BUNDLE",
			[("TEST-SW-COMP-A", 2, "Nos"), ("TEST-SW-COMP-B", 1, "Nos")],
		)
		# 300*2 + 500*1 = 1100
		self.assertEqual(get_shipping_weight_g("TEST-SW-BUNDLE", "Nos"), 1100)

	def test_bundle_applies_component_pack_uom_factor(self):
		create_item("TEST-SW-COMP-PACK", weight_per_unit=100, weight_uom="Gram", pack_factor=6)
		create_bundle("TEST-SW-BUNDLE-PACK", [("TEST-SW-COMP-PACK", 1, PACK6_UOM)])
		# 100g * qty 1 * pack factor 6 = 600
		self.assertEqual(get_shipping_weight_g("TEST-SW-BUNDLE-PACK", "Nos"), 600)

	def test_bundle_with_missing_component_weight_is_zero(self):
		create_item("TEST-SW-COMP-OK", weight_per_unit=300, weight_uom="Gram")
		create_item("TEST-SW-COMP-NOWT")
		create_bundle(
			"TEST-SW-BUNDLE-INCOMPLETE",
			[("TEST-SW-COMP-OK", 1, "Nos"), ("TEST-SW-COMP-NOWT", 1, "Nos")],
		)
		self.assertEqual(get_shipping_weight_g("TEST-SW-BUNDLE-INCOMPLETE", "Nos"), 0)


class TestProxySkuWeightAutofill(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_item("TEST-SW-AUTOFILL", weight_per_unit=300, weight_uom="Gram")
		create_item("TEST-SW-AUTOFILL-BAD", weight_per_unit=450, weight_uom="Nos")

	def make_proxy_sku(self, item_code, uom="Nos", shipping_weight_g=None):
		proxy_sku = frappe.new_doc("Proxy SKU")
		proxy_sku.item = item_code
		proxy_sku.uom = uom
		proxy_sku.is_active = 1
		if shipping_weight_g is not None:
			proxy_sku.shipping_weight_g = shipping_weight_g
		proxy_sku.insert()
		return proxy_sku

	def test_validate_autofills_from_item(self):
		proxy_sku = self.make_proxy_sku("TEST-SW-AUTOFILL")
		self.assertEqual(proxy_sku.shipping_weight_g, 300)

	def test_validate_does_not_overwrite_manual_value(self):
		proxy_sku = self.make_proxy_sku("TEST-SW-AUTOFILL", shipping_weight_g=999)
		self.assertEqual(proxy_sku.shipping_weight_g, 999)

	def test_validate_leaves_blank_for_unusable_item_weight(self):
		proxy_sku = self.make_proxy_sku("TEST-SW-AUTOFILL-BAD")
		self.assertEqual(proxy_sku.shipping_weight_g, 0)
