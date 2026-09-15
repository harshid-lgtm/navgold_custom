# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today
from frappe.utils.data import cstr

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
	SHIPPING_GROUPS,
	build_inventory_patches,
	get_sku_inventory_data,
)

# inventory_for_backorder threads lead_time_map={"TEST-SUPPLIER": 2}, so the supplier lead time is 2 days.
SUPPLIER_LEAD_TIME_DAYS = 2
OPEN_PO_RESTOCK_DAYS_AHEAD = 30
TEST_MARKETPLACE_ID = "A1F83G8C2ARO7P"


def emitted_fulfillment(inventory):
	for patch in build_inventory_patches(inventory, TEST_MARKETPLACE_ID):
		if patch["path"].endswith("fulfillment_availability"):
			return patch["value"][0]
	return {}


def backorder_pricing():
	return frappe._dict(
		proxy_sku="TEST-PROXY-SKU",
		active_strategy="TEST-STRAT-BACKORDER",
		fba_strategy="TEST-STRAT-FBA",
		sfp_strategy="TEST-STRAT-SFP",
		backorder_strategy="TEST-STRAT-BACKORDER",
	)


def inventory_for_backorder(supplier_sfp_eligible, row_sfp, has_restock_date, supplier_stock=5):
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
			"restock_date": (
				cstr(add_days(today(), OPEN_PO_RESTOCK_DAYS_AHEAD)) if has_restock_date else None
			),
		}
	}
	resolved_supplier = frappe._dict(
		supplier="TEST-SUPPLIER", stock_qty=supplier_stock, rank=1, price=10, sfp=1 if row_sfp else 0
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


def inventory_for_default_in_stock():
	# A non-backorder listing (active strategy is neither FBA/SFP/backorder) selling from own warehouse
	# stock — the path that still emits the real supplier handling time.
	pricing = frappe._dict(
		proxy_sku="TEST-PROXY-SKU",
		active_strategy="TEST-STRAT-DEFAULT",
		fba_strategy="TEST-STRAT-FBA",
		sfp_strategy="TEST-STRAT-SFP",
		backorder_strategy="TEST-STRAT-BACKORDER",
	)
	proxy_sku_info = {
		"TEST-PROXY-SKU": {
			"item": "TEST-ITEM",
			"uom": "Nos",
			"pool_size": 1,
			"pack_size": 1.0,
			"merchant_shipping_group": None,
		}
	}
	stock_info_map = {"TEST-PROXY-SKU": {"mfn_stock": 10, "has_restock_date": False, "restock_date": None}}
	resolved_supplier = frappe._dict(supplier="TEST-SUPPLIER", stock_qty=5, rank=1, price=10, sfp=0)
	supplier_data_map = {
		"TEST-ITEM": {"top_supplier": resolved_supplier, "in_stock_supplier": resolved_supplier}
	}

	return get_sku_inventory_data(
		pricing,
		zero_stock_proxy_skus=None,
		proxy_sku_info=proxy_sku_info,
		stock_info_map=stock_info_map,
		supplier_data_map=supplier_data_map,
		lead_time_map={"TEST-SUPPLIER": 2},
		bundle_buildable_map={},
		supplier_sfp_eligible_map={"TEST-SUPPLIER": False},
	)


class TestSfpBackorderItemOptOut(IntegrationTestCase):
	def test_eligible_supplier_row_sfp_with_restock_date_lists_as_sfp(self):
		inventory = inventory_for_backorder(supplier_sfp_eligible=True, row_sfp=True, has_restock_date=True)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["sfp"])
		self.assertTrue(inventory["send_lead_time"])
		self.assertIsNotNone(inventory["restock_date"])

	def test_row_sfp_off_stays_main_revised(self):
		inventory = inventory_for_backorder(supplier_sfp_eligible=True, row_sfp=False, has_restock_date=True)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["main_revised"])

	def test_supplier_flag_off_stays_main_revised_even_if_row_sfp_on(self):
		inventory = inventory_for_backorder(supplier_sfp_eligible=False, row_sfp=True, has_restock_date=True)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["main_revised"])

	def test_no_restock_but_supplier_in_stock_lists_as_sfp(self):
		inventory = inventory_for_backorder(
			supplier_sfp_eligible=True, row_sfp=True, has_restock_date=False, supplier_stock=5
		)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["sfp"])

	def test_no_restock_and_no_supplier_stock_stays_main_revised(self):
		# Neither a restock date nor supplier stock => unfulfillable now, so Main Revised.
		inventory = inventory_for_backorder(
			supplier_sfp_eligible=True, row_sfp=True, has_restock_date=False, supplier_stock=0
		)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["main_revised"])


class TestBackorderSupplierRestockDate(IntegrationTestCase):
	# Supplier-backed backorder with no open PO must still carry a restock date. Without it Amazon reads
	# the qty as ship-now and promises immediate dispatch instead of a deferred backorder window.
	def test_main_revised_backorder_sends_supplier_lead_time_restock_date(self):
		inventory = inventory_for_backorder(
			supplier_sfp_eligible=False, row_sfp=True, has_restock_date=False, supplier_stock=5
		)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["main_revised"])
		self.assertEqual(inventory["restock_date"], cstr(add_days(today(), SUPPLIER_LEAD_TIME_DAYS)))
		self.assertTrue(inventory["send_lead_time"])

	def test_sfp_backorder_sends_supplier_lead_time_restock_date(self):
		inventory = inventory_for_backorder(
			supplier_sfp_eligible=True, row_sfp=True, has_restock_date=False, supplier_stock=5
		)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["sfp"])
		self.assertEqual(inventory["restock_date"], cstr(add_days(today(), SUPPLIER_LEAD_TIME_DAYS)))
		self.assertTrue(inventory["send_lead_time"])

	def test_no_supplier_stock_leaves_restock_date_unset(self):
		inventory = inventory_for_backorder(
			supplier_sfp_eligible=True, row_sfp=True, has_restock_date=False, supplier_stock=0
		)
		self.assertIsNone(inventory["restock_date"])

	def test_supplier_lead_time_wins_over_open_po_restock_date(self):
		inventory = inventory_for_backorder(
			supplier_sfp_eligible=True, row_sfp=True, has_restock_date=True, supplier_stock=5
		)
		self.assertEqual(inventory["restock_date"], cstr(add_days(today(), SUPPLIER_LEAD_TIME_DAYS)))


class TestBackorderHandlingTime(IntegrationTestCase):
	# The supplier lead time is already priced into restock_date; handling time must not double-count it.
	# Assert the actual emitted fulfillment patch, not just the intermediate dict.
	def test_main_revised_backorder_emits_nominal_one_day_handling(self):
		inventory = inventory_for_backorder(
			supplier_sfp_eligible=False, row_sfp=True, has_restock_date=False, supplier_stock=5
		)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["main_revised"])
		fulfillment = emitted_fulfillment(inventory)
		self.assertEqual(fulfillment["lead_time_to_ship_max_days"], 1)
		self.assertEqual(fulfillment["restock_date"], cstr(add_days(today(), SUPPLIER_LEAD_TIME_DAYS)))

	def test_sfp_backorder_emits_nominal_one_day_handling(self):
		inventory = inventory_for_backorder(
			supplier_sfp_eligible=True, row_sfp=True, has_restock_date=False, supplier_stock=5
		)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["sfp"])
		fulfillment = emitted_fulfillment(inventory)
		self.assertEqual(fulfillment["lead_time_to_ship_max_days"], 1)
		self.assertEqual(fulfillment["restock_date"], cstr(add_days(today(), SUPPLIER_LEAD_TIME_DAYS)))

	def test_non_backorder_listing_keeps_real_handling_time(self):
		inventory = inventory_for_default_in_stock()
		fulfillment = emitted_fulfillment(inventory)
		self.assertEqual(fulfillment["lead_time_to_ship_max_days"], SUPPLIER_LEAD_TIME_DAYS)


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


def make_sips(supplier, item):
	return frappe.get_doc(
		{
			"doctype": "Supplier Item Price and Stock",
			"item": item,
			"supplier": supplier,
			"uom": "Nos",
			"stock_qty": 5,
			"currency": "GBP",
			"price": 10,
		}
	).insert()


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

	def test_row_can_be_opted_out_after_creation(self):
		supplier = make_supplier("_Test SFP Optout Supplier", custom_sfp_eligible=1)
		item = make_item("_Test SFP Item D")
		row = make_sips(supplier.name, item)
		self.assertEqual(row.sfp, 1)

		row.sfp = 0
		row.save()
		self.assertEqual(frappe.db.get_value("Supplier Item Price and Stock", row.name, "sfp"), 0)

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

		supplier = make_supplier("_Test SFP Backfill Supplier", custom_sfp_eligible=0)
		item = make_item("_Test SFP Item E")
		row = make_sips(supplier.name, item)
		self.assertEqual(row.sfp, 0)

		# Mark the supplier prime without the propagation hook, mirroring a pre-patch row.
		frappe.db.set_value("Supplier", supplier.name, "custom_sfp_eligible", 1)
		execute()

		self.assertEqual(frappe.db.get_value("Supplier Item Price and Stock", row.name, "sfp"), 1)

		inventory = inventory_for_backorder(supplier_sfp_eligible=True, row_sfp=True, has_restock_date=True)
		self.assertEqual(inventory["shipping_group"], SHIPPING_GROUPS["sfp"])
