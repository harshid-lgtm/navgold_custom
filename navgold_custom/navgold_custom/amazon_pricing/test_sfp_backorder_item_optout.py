# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
	SHIPPING_GROUPS,
	get_sku_inventory_data,
)


def backorder_pricing():
	return frappe._dict(
		proxy_sku="TEST-PROXY-SKU",
		active_strategy="TEST-STRAT-BACKORDER",
		fba_strategy="TEST-STRAT-FBA",
		sfp_strategy="TEST-STRAT-SFP",
		backorder_strategy="TEST-STRAT-BACKORDER",
	)


def inventory_for_backorder(supplier_sfp_eligible, row_sfp, has_restock_date):
	proxy_sku_info = {
		"TEST-PROXY-SKU": {
			"item": "TEST-ITEM",
			"uom": "Nos",
			"pool_size": 1,
			"pack_size": 1.0,
			"merchant_shipping_group": None,
		}
	}
	stock_info_map = {
		"TEST-PROXY-SKU": {
			"mfn_stock": 0,
			"has_restock_date": has_restock_date,
			"restock_date": "2026-07-10" if has_restock_date else None,
		}
	}
	resolved_supplier = frappe._dict(
		supplier="TEST-SUPPLIER", stock_qty=5, rank=1, price=10, sfp=1 if row_sfp else 0
	)
	supplier_data_map = {
		"TEST-ITEM": {"top_supplier": resolved_supplier, "in_stock_supplier": resolved_supplier}
	}

	return get_sku_inventory_data(
		backorder_pricing(),
		zero_stock_proxy_skus=None,
		proxy_sku_info=proxy_sku_info,
		stock_info_map=stock_info_map,
		supplier_data_map=supplier_data_map,
		lead_time_map={"TEST-SUPPLIER": 2},
		bundle_buildable_map={},
		supplier_sfp_eligible_map={"TEST-SUPPLIER": supplier_sfp_eligible},
	)


class TestSfpBackorderItemOptOut(IntegrationTestCase):
	def test_eligible_supplier_row_sfp_with_restock_date_lists_as_sfp(self):
		inventory = inventory_for_backorder(supplier_sfp_eligible=True, row_sfp=True, has_restock_date=True)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["sfp"])

	def test_row_sfp_off_stays_main_revised(self):
		inventory = inventory_for_backorder(supplier_sfp_eligible=True, row_sfp=False, has_restock_date=True)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["main_revised"])

	def test_supplier_flag_off_stays_main_revised_even_if_row_sfp_on(self):
		inventory = inventory_for_backorder(supplier_sfp_eligible=False, row_sfp=True, has_restock_date=True)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["main_revised"])

	def test_no_restock_date_stays_main_revised(self):
		inventory = inventory_for_backorder(supplier_sfp_eligible=True, row_sfp=True, has_restock_date=False)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["main_revised"])


def make_supplier(name, custom_sfp_eligible):
	if frappe.db.exists("Supplier", name):
		frappe.delete_doc("Supplier", name, force=True)

	supplier = frappe.new_doc("Supplier")
	supplier.supplier_name = name
	supplier.supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0})
	supplier.custom_rank = 1
	supplier.custom_sfp_eligible = custom_sfp_eligible
	for day in (
		"Sunday",
		"Monday",
		"Tuesday",
		"Wednesday",
		"Thursday",
		"Friday",
		"Saturday",
	):
		supplier.append("custom_lead_time", {"day": day, "lead_time_before_12": 1, "lead_time_after_12": 2})
	supplier.insert()
	return supplier


def make_item(item_code):
	if not frappe.db.exists("Item", item_code):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": item_code,
				"item_group": frappe.db.get_value("Item Group", {"is_group": 0}),
				"stock_uom": "Nos",
				"is_stock_item": 1,
			}
		).insert()
	return item_code


SFP_OMITTED = object()


def make_sips(supplier, item, sfp=SFP_OMITTED):
	data = {
		"doctype": "Supplier Item Price and Stock",
		"item": item,
		"supplier": supplier,
		"uom": "Nos",
		"stock_qty": 5,
		"currency": "GBP",
		"price": 10,
	}
	# Only set sfp when the caller provides it, so an omitted value stays genuinely
	# unset and exercises the supplier-inheritance path.
	if sfp is not SFP_OMITTED:
		data["sfp"] = sfp
	return frappe.get_doc(data).insert()


class TestSfpInheritanceAndPropagation(IntegrationTestCase):
	def test_unset_sfp_under_prime_supplier_inherits_on(self):
		supplier = make_supplier("_Test SFP Prime Supplier", custom_sfp_eligible=1)
		item = make_item("_Test SFP Item A")
		row = make_sips(supplier.name, item)
		self.assertEqual(row.sfp, 1)

	def test_unset_sfp_under_non_prime_supplier_inherits_off(self):
		supplier = make_supplier("_Test SFP Plain Supplier", custom_sfp_eligible=0)
		item = make_item("_Test SFP Item B")
		row = make_sips(supplier.name, item)
		self.assertEqual(row.sfp, 0)

	def test_explicit_sfp_off_under_prime_supplier_stays_off(self):
		supplier = make_supplier("_Test SFP Optout Supplier", custom_sfp_eligible=1)
		item = make_item("_Test SFP Item D")
		row = make_sips(supplier.name, item, sfp=0)
		self.assertEqual(row.sfp, 0)

	def test_marking_supplier_prime_propagates_to_existing_rows(self):
		supplier = make_supplier("_Test SFP Toggle Supplier", custom_sfp_eligible=0)
		item = make_item("_Test SFP Item C")
		row = make_sips(supplier.name, item)
		self.assertEqual(row.sfp, 0)

		supplier.custom_sfp_eligible = 1
		supplier.save()

		self.assertEqual(frappe.db.get_value("Supplier Item Price and Stock", row.name, "sfp"), 1)

	def test_backfill_patch_marks_existing_prime_supplier_rows(self):
		from navgold_custom.patches.v1.backfill_sfp_eligible_on_supplier_items import execute

		supplier = make_supplier("_Test SFP Backfill Supplier", custom_sfp_eligible=1)
		item = make_item("_Test SFP Item E")
		# Pre-existing row left at sfp=0, as every row was after the field's initial migration.
		row = make_sips(supplier.name, item, sfp=0)
		self.assertEqual(row.sfp, 0)

		execute()

		self.assertEqual(frappe.db.get_value("Supplier Item Price and Stock", row.name, "sfp"), 1)

		inventory = inventory_for_backorder(supplier_sfp_eligible=True, row_sfp=True, has_restock_date=True)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["sfp"])
