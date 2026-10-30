# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, change_settings

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import get_sku_inventory_data
from navgold_custom.navgold_custom.amazon_pricing.strategy_selector import (
	get_stock_info,
	get_stock_info_batch,
	select_strategy,
)
from navgold_custom.navgold_custom.amazon_pricing.template_applicator import apply_template_to_proxy_sku
from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import (
	get_stock_warehouse,
	make_customer,
	receive_stock,
)
from navgold_custom.navgold_custom.amazon_pricing.test_template_applicator import create_strategy_template
from navgold_custom.navgold_custom.utils import (
	ITEM_AFN_FIELD,
	calculate_adjusted_qty,
	get_fba_stock_map,
)


def create_test_item():
	item = frappe.new_doc("Item")
	item.item_code = f"TEST-AFN-{frappe.generate_hash(length=8)}"
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 1
	item.insert()
	return item.name


def create_fba_fixture(template_name):
	item_code = create_test_item()
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.item = item_code
	proxy_sku.uom = "Nos"
	proxy_sku.is_fba = 1
	proxy_sku.insert()
	apply_template_to_proxy_sku(template_name, proxy_sku.name)
	return item_code, proxy_sku.name


def ensure_uom(uom_name):
	if not frappe.db.exists("UOM", uom_name):
		frappe.get_doc({"doctype": "UOM", "uom_name": uom_name}).insert()
	return uom_name


def create_pack_item(pack_uom, conversion_factor):
	item = frappe.new_doc("Item")
	item.item_code = f"TEST-PACK-{frappe.generate_hash(length=8)}"
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 1
	item.append("uoms", {"uom": pack_uom, "conversion_factor": conversion_factor})
	item.insert()
	return item.name


def create_proxy_sku_for(item_code, uom):
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.item = item_code
	proxy_sku.uom = uom
	proxy_sku.is_active = 1
	proxy_sku.insert()
	return proxy_sku.name


class TestPackAllocationStrategy(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.template_name = create_strategy_template()

	def test_calculate_adjusted_qty_floors_per_pool_and_pack(self):
		# 4 base units shared across 5 SKUs at pack-of-6: no full pack can be allocated.
		self.assertEqual(calculate_adjusted_qty(4, 5, 6), 0)
		# Single SKU, pack-of-3, 4 base units: one full pack fits.
		self.assertEqual(calculate_adjusted_qty(4, 1, 3), 1)
		self.assertEqual(calculate_adjusted_qty(4, 0, 3), 0)
		self.assertEqual(calculate_adjusted_qty(4, 1, 0), 0)

	def test_no_full_pack_falls_through_to_backorder(self):
		pack_uom = ensure_uom(f"NG Pack 6 {frappe.generate_hash(length=6)}")
		item_code = create_pack_item(pack_uom, 6)

		under_test = create_proxy_sku_for(item_code, pack_uom)
		# Four sibling Proxy SKUs share the same physical stock: pool size becomes five.
		for index in range(4):
			sibling_uom = ensure_uom(f"NG Sibling {index} {frappe.generate_hash(length=6)}")
			create_proxy_sku_for(item_code, sibling_uom)

		apply_template_to_proxy_sku(self.template_name, under_test)

		proxy = frappe.get_cached_doc("Proxy SKU", under_test)
		self.assertEqual(proxy.uom, pack_uom)

		with patch(
			"navgold_custom.navgold_custom.amazon_pricing.strategy_selector.get_free_qty_map",
			return_value={item_code: {"free_qty": 4, "actual_qty": 4}},
		):
			stock_info = get_stock_info(under_test)

		# mfn_stock stays raw (feed contract); only total_stock is pack-aware.
		# The four pack-of-1 siblings take a unit each first, so no whole pack-of-6 is left.
		self.assertEqual(stock_info["mfn_stock"], 4)
		self.assertEqual(stock_info["total_stock"], 0)

		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": under_test})
		# Pre-fix this reported total_stock 4 > 0 and wrongly selected SFP; now it backorders.
		self.assertEqual(select_strategy(pricing, stock_info=stock_info), pricing.backorder_strategy)

	def test_feed_quantity_not_double_divided(self):
		pack_uom = ensure_uom(f"NG Pack 6 Feed {frappe.generate_hash(length=6)}")
		item_code = create_pack_item(pack_uom, 6)

		under_test = create_proxy_sku_for(item_code, pack_uom)
		for index in range(4):
			sibling_uom = ensure_uom(f"NG Feed Sibling {index} {frappe.generate_hash(length=6)}")
			create_proxy_sku_for(item_code, sibling_uom)

		apply_template_to_proxy_sku(self.template_name, under_test)

		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": under_test})
		pricing.active_strategy = pricing.default_strategy

		# free_qty 60 over packs 6 + 1 + 1 + 1 + 1: one round costs 10 units, so six whole rounds
		# fit and this SKU publishes 6 packs. Anything smaller means the feed divided twice.
		with patch(
			"navgold_custom.navgold_custom.amazon_pricing.strategy_selector.get_free_qty_map",
			return_value={item_code: {"free_qty": 60, "actual_qty": 60}},
		):
			inventory_data = get_sku_inventory_data(pricing)

		self.assertEqual(inventory_data["quantity"], 6)

	def test_select_strategy_backorders_on_zero_total_stock(self):
		pack_uom = ensure_uom(f"NG Pack 2 {frappe.generate_hash(length=6)}")
		item_code = create_pack_item(pack_uom, 2)
		proxy_sku = create_proxy_sku_for(item_code, pack_uom)
		apply_template_to_proxy_sku(self.template_name, proxy_sku)

		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": proxy_sku})
		stock_info = {"fba_stock": 0, "total_stock": 0, "has_restock_date": False}
		self.assertEqual(select_strategy(pricing, stock_info=stock_info), pricing.backorder_strategy)



class TestFreeStockAmazonSwitch(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.template_name = create_strategy_template()

	@change_settings(
		"Navgold Settings",
		{"enable_freestock_for_amazon": 0, "enable_afn_stock_sync": 0},
	)
	def test_disabled_freestock_zeroes_single_and_batch_mfn_stock(self):
		item_code = create_test_item()
		proxy_sku = create_proxy_sku_for(item_code, "Nos")
		apply_template_to_proxy_sku(self.template_name, proxy_sku)

		with patch(
			"navgold_custom.navgold_custom.amazon_pricing.strategy_selector.get_free_qty_map",
			return_value={item_code: {"free_qty": 50, "actual_qty": 50}},
		):
			stock_info = get_stock_info(proxy_sku)
			batch_info = get_stock_info_batch([proxy_sku])[proxy_sku]

		self.assertEqual(stock_info["mfn_stock"], 0)
		self.assertEqual(stock_info["total_stock"], 0)
		self.assertEqual(batch_info["mfn_stock"], 0)
		self.assertEqual(batch_info["total_stock"], 0)

		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": proxy_sku})
		self.assertEqual(select_strategy(pricing, stock_info=stock_info), pricing.backorder_strategy)

	@change_settings(
		"Navgold Settings",
		{"enable_freestock_for_amazon": 0, "enable_afn_stock_sync": 1},
	)
	def test_disabled_freestock_does_not_zero_fba_stock(self):
		_item_code, proxy_sku = create_fba_fixture(self.template_name)
		frappe.db.set_value("Proxy SKU", proxy_sku, "afn_stock", 7, update_modified=False)
		frappe.clear_document_cache("Proxy SKU", proxy_sku)

		stock_info = get_stock_info(proxy_sku)
		self.assertEqual(stock_info["mfn_stock"], 0)
		self.assertEqual(stock_info["fba_stock"], 7)

		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": proxy_sku})
		self.assertEqual(select_strategy(pricing, stock_info=stock_info), pricing.fba_strategy)



class TestStrategySelectorAFNBridge(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.template_name = create_strategy_template()

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 1})
	def test_per_sku_fetched_stock_selects_fba(self):
		_item_code, proxy_sku = create_fba_fixture(self.template_name)
		# FBA stock is now sourced from the SKU's own afn_stock, not the item-level aggregate.
		frappe.db.set_value("Proxy SKU", proxy_sku, "afn_stock", 5, update_modified=False)
		frappe.clear_document_cache("Proxy SKU", proxy_sku)

		stock_info = get_stock_info(proxy_sku)
		self.assertEqual(stock_info["fba_stock"], 5)

		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": proxy_sku})
		self.assertEqual(select_strategy(pricing), pricing.fba_strategy)

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 1})
	def test_batch_path_uses_per_sku_fetched_stock(self):
		_item_code, proxy_sku = create_fba_fixture(self.template_name)
		frappe.db.set_value("Proxy SKU", proxy_sku, "afn_stock", 3, update_modified=False)

		stock_info_map = get_stock_info_batch([proxy_sku])
		self.assertEqual(stock_info_map[proxy_sku]["fba_stock"], 3)

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 0})
	def test_sync_off_zeroes_fba_stock(self):
		_item_code, proxy_sku = create_fba_fixture(self.template_name)
		# With sync off, afn_stock is never refreshed, so the stale per-SKU value must be ignored
		# and FBA treated as out of stock on both the single and batch paths.
		frappe.db.set_value("Proxy SKU", proxy_sku, "afn_stock", 5, update_modified=False)
		frappe.clear_document_cache("Proxy SKU", proxy_sku)

		stock_info = get_stock_info(proxy_sku)
		self.assertEqual(stock_info["fba_stock"], 0)

		batch_info = get_stock_info_batch([proxy_sku])
		self.assertEqual(batch_info[proxy_sku]["fba_stock"], 0)

		# No stock anywhere and no restock date drops to the backorder strategy, never FBA.
		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": proxy_sku})
		self.assertEqual(select_strategy(pricing), pricing.backorder_strategy)

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 1})
	def test_sync_on_takes_max_of_bin_and_fetched(self):
		item_code = create_test_item()
		frappe.db.set_value("Item", item_code, ITEM_AFN_FIELD, 3, update_modified=False)

		# Bin holds in-flight FBA Transfers Amazon does not count as fulfillable yet.
		with patch(
			"navgold_custom.navgold_custom.utils.get_afn_warehouse_stock_map",
			return_value={item_code: 10.0},
		):
			stock_map = get_fba_stock_map([item_code], "Any Warehouse")
		self.assertEqual(stock_map[item_code], 10.0)

		frappe.db.set_value("Item", item_code, ITEM_AFN_FIELD, 12, update_modified=False)
		with patch(
			"navgold_custom.navgold_custom.utils.get_afn_warehouse_stock_map",
			return_value={item_code: 10.0},
		):
			stock_map = get_fba_stock_map([item_code], "Any Warehouse")
		self.assertEqual(stock_map[item_code], 12.0)


class TestFBAStockPerProxySKU(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.template_name = create_strategy_template()

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 1})
	def test_sibling_fba_stock_does_not_leak_into_mfn_sku(self):
		# MRG1 regression: one item, an FBA SKU holding its own afn_stock and an MFN sibling with
		# none. The item-level aggregate carries the FBA sibling's stock (48); the MFN SKU must read
		# its own afn_stock (0) and never ride the sibling into the FBA strategy.
		pack_uom = ensure_uom(f"NG MRG {frappe.generate_hash(length=6)}")
		item_code = create_pack_item(pack_uom, 3)
		frappe.db.set_value("Item", item_code, ITEM_AFN_FIELD, 48, update_modified=False)

		fba_proxy = frappe.new_doc("Proxy SKU")
		fba_proxy.item = item_code
		fba_proxy.uom = "Nos"
		fba_proxy.is_fba = 1
		fba_proxy.afn_stock = 48
		fba_proxy.insert()
		apply_template_to_proxy_sku(self.template_name, fba_proxy.name)

		mfn_proxy = frappe.new_doc("Proxy SKU")
		mfn_proxy.item = item_code
		mfn_proxy.uom = pack_uom
		mfn_proxy.is_fba = 0
		mfn_proxy.afn_stock = 0
		mfn_proxy.insert()
		apply_template_to_proxy_sku(self.template_name, mfn_proxy.name)

		fba_pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": fba_proxy.name})
		self.assertEqual(select_strategy(fba_pricing), fba_pricing.fba_strategy)

		mfn_stock_info = get_stock_info(mfn_proxy.name)
		self.assertEqual(mfn_stock_info["fba_stock"], 0)

		mfn_pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": mfn_proxy.name})
		selected_strategy = select_strategy(mfn_pricing)
		self.assertNotEqual(selected_strategy, mfn_pricing.fba_strategy)
		self.assertEqual(selected_strategy, mfn_pricing.backorder_strategy)


class TestStrategyLadderIgnoresDraftOrders(IntegrationTestCase):
	"""The ladder is a question about physical supply: Amazon's shelf, then ours, then the supplier.
	A draft Sales Order reserves nothing in Bin and is not stock, so it must not move a SKU down a
	rung — that only publishes a backorder price for stock sitting in the warehouse."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.template_name = create_strategy_template()
		cls.warehouse, cls.company = get_stock_warehouse()
		cls.customer = make_customer(f"NG Ladder Draft {frappe.generate_hash(length=6)}")

	def build_sku(self, warehouse_qty=0, fba_qty=0):
		item_code = create_test_item()
		if warehouse_qty:
			receive_stock(item_code, warehouse_qty, self.warehouse, self.company)

		proxy_sku = frappe.new_doc("Proxy SKU")
		proxy_sku.item = item_code
		proxy_sku.uom = "Nos"
		proxy_sku.is_active = 1
		proxy_sku.afn_stock = fba_qty
		proxy_sku.insert()
		apply_template_to_proxy_sku(self.template_name, proxy_sku.name)
		return item_code, proxy_sku.name

	def sell_on_a_draft_order(self, item_code, qty):
		sales_order = frappe.new_doc("Sales Order")
		sales_order.customer = self.customer
		sales_order.company = self.company
		sales_order.transaction_date = frappe.utils.today()
		sales_order.delivery_date = frappe.utils.today()
		sales_order.amazon_order_id = f"TEST-LADDER-{frappe.generate_hash(length=8)}"
		sales_order.fulfillment_channel = "MFN"
		sales_order.amazon_order_status = "Pending"
		sales_order.append(
			"items",
			{
				"item_code": item_code,
				"qty": qty,
				"rate": 10,
				"warehouse": self.warehouse,
				"delivery_date": frappe.utils.today(),
			},
		)
		sales_order.insert()
		return sales_order.name

	def strategy_for(self, proxy_sku):
		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": proxy_sku})
		return pricing, select_strategy(pricing, stock_info=get_stock_info(proxy_sku))

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 1})
	def test_fba_stock_wins_the_ladder(self):
		item_code, proxy_sku = self.build_sku(warehouse_qty=5, fba_qty=7)
		self.sell_on_a_draft_order(item_code, 7)

		pricing, strategy = self.strategy_for(proxy_sku)
		self.assertEqual(strategy, pricing.fba_strategy, "Amazon holds 7, so the SKU ships FBA")

	def test_warehouse_stock_takes_sfp_even_with_a_draft_order_against_it(self):
		item_code, proxy_sku = self.build_sku(warehouse_qty=10)
		self.sell_on_a_draft_order(item_code, 10)

		pricing, strategy = self.strategy_for(proxy_sku)
		self.assertEqual(
			strategy,
			pricing.sfp_strategy,
			"10 units are on the shelf; a draft order is not a reservation and must not backorder them",
		)

	def test_no_stock_anywhere_falls_to_backorder(self):
		item_code, proxy_sku = self.build_sku()

		pricing, strategy = self.strategy_for(proxy_sku)
		self.assertEqual(strategy, pricing.backorder_strategy, "nothing on either shelf")

	def test_the_feed_publishes_shelf_stock_not_shelf_minus_drafts(self):
		item_code, proxy_sku = self.build_sku(warehouse_qty=10)
		self.sell_on_a_draft_order(item_code, 4)

		self.assertEqual(
			get_stock_info(proxy_sku)["mfn_stock"],
			10,
			"the feed publishes what is physically sellable off the shelf",
		)
