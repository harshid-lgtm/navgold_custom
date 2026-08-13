# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, change_settings

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
	build_proxy_sku_info_map,
	get_sku_inventory_data,
)
from navgold_custom.navgold_custom.amazon_pricing.strategy_selector import (
	get_stock_info,
	get_stock_info_batch,
	select_strategy,
)
from navgold_custom.navgold_custom.amazon_pricing.template_applicator import apply_template_to_proxy_sku
from navgold_custom.navgold_custom.amazon_pricing.test_template_applicator import create_strategy_template
from navgold_custom.navgold_custom.utils import allocate_pack_stock

FREE_QTY_TARGET = "navgold_custom.navgold_custom.amazon_pricing.strategy_selector.get_free_qty_map"


def ensure_uom(uom_name):
	if not frappe.db.exists("UOM", uom_name):
		frappe.get_doc({"doctype": "UOM", "uom_name": uom_name}).insert()
	return uom_name


def create_pack_pool(pack_sizes, template_name):
	item = frappe.new_doc("Item")
	item.item_code = f"TEST-SHARE-{frappe.generate_hash(length=8)}"
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 1

	pack_uoms = []
	for pack_size in pack_sizes:
		pack_uom = ensure_uom(f"NG Share {pack_size} {frappe.generate_hash(length=6)}")
		item.append("uoms", {"uom": pack_uom, "conversion_factor": pack_size})
		pack_uoms.append(pack_uom)
	item.insert()

	proxy_sku_by_pack_size = {}
	for pack_size, pack_uom in zip(pack_sizes, pack_uoms, strict=True):
		proxy_sku = frappe.new_doc("Proxy SKU")
		proxy_sku.item = item.name
		proxy_sku.uom = pack_uom
		proxy_sku.is_active = 1
		proxy_sku.is_fba = 0
		proxy_sku.insert()
		apply_template_to_proxy_sku(template_name, proxy_sku.name)
		proxy_sku_by_pack_size[pack_size] = proxy_sku.name

	return item.name, proxy_sku_by_pack_size


def make_fba(proxy_sku_name, afn_stock):
	frappe.db.set_value(
		"Proxy SKU", proxy_sku_name, {"is_fba": 1, "afn_stock": afn_stock}, update_modified=False
	)
	frappe.clear_document_cache("Proxy SKU", proxy_sku_name)


class TestAllocatePackStock(IntegrationTestCase):
	def test_ticket_0576_five_units_across_packs_of_2_3_6(self):
		allocation = allocate_pack_stock(5, {"x2": 2, "x3": 3, "x6": 6})
		self.assertEqual(allocation, {"x2": 1, "x3": 1, "x6": 0})

	def test_round_robin_repeats_until_stock_runs_out(self):
		allocation = allocate_pack_stock(100, {"x2": 2, "x3": 3, "x6": 6})
		self.assertEqual(allocation, {"x2": 9, "x3": 9, "x6": 9})

	def test_never_promises_more_units_than_available(self):
		pack_pools = [
			{"x2": 2, "x3": 3, "x6": 6},
			{"x1": 1},
			{"x1": 1, "x12": 12},
			{"x4": 4, "x4b": 4, "x5": 5},
			{"x7": 7, "x0": 0},
		]
		quantities = [0, 0.5, 1, 2, 4, 4.998, 5, 5.9, 7, 10, 11, 12, 99, 100, 101, 1000, 12345.67]
		for pack_size_by_proxy_sku in pack_pools:
			for available_qty in quantities:
				allocation = allocate_pack_stock(available_qty, pack_size_by_proxy_sku)
				promised_units = sum(
					packs * pack_size_by_proxy_sku[proxy_sku] for proxy_sku, packs in allocation.items()
				)
				self.assertLessEqual(
					promised_units,
					available_qty,
					f"oversold {pack_size_by_proxy_sku} from {available_qty}: {allocation}",
				)

	def test_fractional_free_qty_never_rounds_up(self):
		# 4.998 units buy two packs of 2 and nothing else: the 0.998 dust must never become a pack.
		self.assertEqual(allocate_pack_stock(4.998, {"x2": 2, "x3": 3}), {"x2": 2, "x3": 0})
		# 5.9 covers one round of 2 + 3; the leftover 0.9 again buys nothing.
		self.assertEqual(allocate_pack_stock(5.9, {"x2": 2, "x3": 3}), {"x2": 1, "x3": 1})

	def test_ties_break_on_proxy_sku_name_and_stay_stable(self):
		pack_size_by_proxy_sku = {"sku-b": 2, "sku-a": 2}
		first_run = allocate_pack_stock(2, pack_size_by_proxy_sku)
		second_run = allocate_pack_stock(2, dict(reversed(list(pack_size_by_proxy_sku.items()))))
		self.assertEqual(first_run, {"sku-a": 1, "sku-b": 0})
		self.assertEqual(first_run, second_run)

	def test_zero_and_missing_pack_sizes_get_nothing(self):
		self.assertEqual(allocate_pack_stock(10, {"x0": 0}), {"x0": 0})
		self.assertEqual(allocate_pack_stock(-5, {"x2": 2}), {"x2": 0})
		self.assertEqual(allocate_pack_stock(10, {}), {})


class TestPackStockShare(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.template_name = create_strategy_template()

	def test_smallest_pack_still_gets_stock_when_pool_exceeds_free_qty(self):
		item_code, proxy_sku_by_pack_size = create_pack_pool([2, 3, 6], self.template_name)

		with patch(FREE_QTY_TARGET, return_value={item_code: {"free_qty": 5, "actual_qty": 5}}):
			stock_info_by_pack_size = {
				pack_size: get_stock_info(proxy_sku)
				for pack_size, proxy_sku in proxy_sku_by_pack_size.items()
			}

		self.assertEqual(stock_info_by_pack_size[2]["total_stock"], 1)
		self.assertEqual(stock_info_by_pack_size[3]["total_stock"], 1)
		self.assertEqual(stock_info_by_pack_size[6]["total_stock"], 0)
		# mfn_stock stays the raw base-unit free qty for every SKU: the feed contract depends on it.
		self.assertEqual(stock_info_by_pack_size[2]["mfn_stock"], 5)

	def test_stocked_pack_selects_sfp_and_starved_pack_backorders(self):
		item_code, proxy_sku_by_pack_size = create_pack_pool([2, 3, 6], self.template_name)

		with patch(FREE_QTY_TARGET, return_value={item_code: {"free_qty": 5, "actual_qty": 5}}):
			stock_info_by_pack_size = {
				pack_size: get_stock_info(proxy_sku)
				for pack_size, proxy_sku in proxy_sku_by_pack_size.items()
			}

		for pack_size in (2, 3):
			pricing = frappe.get_doc(
				"Amazon Product Pricing", {"proxy_sku": proxy_sku_by_pack_size[pack_size]}
			)
			self.assertEqual(
				select_strategy(pricing, stock_info=stock_info_by_pack_size[pack_size]),
				pricing.sfp_strategy,
			)

		starved_pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": proxy_sku_by_pack_size[6]})
		self.assertEqual(
			select_strategy(starved_pricing, stock_info=stock_info_by_pack_size[6]),
			starved_pricing.backorder_strategy,
		)

	def test_batch_path_matches_single_path(self):
		item_code, proxy_sku_by_pack_size = create_pack_pool([2, 3, 6], self.template_name)
		proxy_sku_names = list(proxy_sku_by_pack_size.values())

		with patch(FREE_QTY_TARGET, return_value={item_code: {"free_qty": 5, "actual_qty": 5}}):
			batch_info = get_stock_info_batch(proxy_sku_names)
			single_info = {proxy_sku: get_stock_info(proxy_sku) for proxy_sku in proxy_sku_names}

		for proxy_sku in proxy_sku_names:
			self.assertEqual(batch_info[proxy_sku]["total_stock"], single_info[proxy_sku]["total_stock"])
			self.assertEqual(batch_info[proxy_sku]["mfn_stock"], single_info[proxy_sku]["mfn_stock"])

	def test_feed_quantities_never_oversell_the_item(self):
		item_code, proxy_sku_by_pack_size = create_pack_pool([2, 3, 6], self.template_name)

		published_by_pack_size = {}
		with patch(FREE_QTY_TARGET, return_value={item_code: {"free_qty": 5, "actual_qty": 5}}):
			for pack_size, proxy_sku in proxy_sku_by_pack_size.items():
				pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": proxy_sku})
				pricing.active_strategy = pricing.default_strategy
				published_by_pack_size[pack_size] = get_sku_inventory_data(pricing)["quantity"]

		self.assertEqual(published_by_pack_size, {2: 1, 3: 1, 6: 0})
		promised_units = sum(packs * pack_size for pack_size, packs in published_by_pack_size.items())
		self.assertLessEqual(promised_units, 5)

	def test_plenty_of_stock_still_fills_every_pack_size(self):
		item_code, proxy_sku_by_pack_size = create_pack_pool([2, 3, 6], self.template_name)

		with patch(FREE_QTY_TARGET, return_value={item_code: {"free_qty": 100, "actual_qty": 100}}):
			total_stock_by_pack_size = {
				pack_size: get_stock_info(proxy_sku)["total_stock"]
				for pack_size, proxy_sku in proxy_sku_by_pack_size.items()
			}

		# 100 units, one round costs 2 + 3 + 6 = 11 units, so nine whole rounds fit.
		self.assertEqual(total_stock_by_pack_size, {2: 9, 3: 9, 6: 9})
		promised_units = sum(packs * pack_size for pack_size, packs in total_stock_by_pack_size.items())
		self.assertLessEqual(promised_units, 100)

	def test_inactive_siblings_do_not_take_warehouse_packs(self):
		item_code, proxy_sku_by_pack_size = create_pack_pool([2, 3], self.template_name)
		frappe.db.set_value("Proxy SKU", proxy_sku_by_pack_size[3], "is_active", 0)

		with patch(FREE_QTY_TARGET, return_value={item_code: {"free_qty": 5, "actual_qty": 5}}):
			total_stock_by_pack_size = {
				pack_size: get_stock_info(proxy_sku)["total_stock"]
				for pack_size, proxy_sku in proxy_sku_by_pack_size.items()
			}

		# Only the pack-of-2 is left in the pool, so it takes two whole packs of the five units.
		self.assertEqual(total_stock_by_pack_size, {2: 2, 3: 0})

	def test_item_without_any_active_sku_keeps_its_share(self):
		item_code, proxy_sku_by_pack_size = create_pack_pool([2, 3], self.template_name)
		for proxy_sku in proxy_sku_by_pack_size.values():
			frappe.db.set_value("Proxy SKU", proxy_sku, "is_active", 0)

		with patch(FREE_QTY_TARGET, return_value={item_code: {"free_qty": 5, "actual_qty": 5}}):
			total_stock_by_pack_size = {
				pack_size: get_stock_info(proxy_sku)["total_stock"]
				for pack_size, proxy_sku in proxy_sku_by_pack_size.items()
			}

		self.assertEqual(total_stock_by_pack_size, {2: 1, 3: 1})

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 1})
	def test_fba_sibling_holding_amazon_stock_takes_no_warehouse_packs(self):
		# Ticket shape: one item listed twice, a pack of 2 on FBA and a pack of 3 on MFN.
		item_code, proxy_sku_by_pack_size = create_pack_pool([2, 3], self.template_name)
		make_fba(proxy_sku_by_pack_size[2], afn_stock=5)

		with patch(FREE_QTY_TARGET, return_value={item_code: {"free_qty": 21, "actual_qty": 21}}):
			stock_info_by_pack_size = {
				pack_size: get_stock_info(proxy_sku)
				for pack_size, proxy_sku in proxy_sku_by_pack_size.items()
			}

		# Amazon still holds stock for the pack of 2, so it sells from there and leaves all 21
		# warehouse units to the pack of 3 — seven whole packs.
		self.assertEqual(stock_info_by_pack_size[3]["total_stock"], 7)
		self.assertEqual(stock_info_by_pack_size[2]["total_stock"], 5)

		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": proxy_sku_by_pack_size[2]})
		self.assertEqual(
			select_strategy(pricing, stock_info=stock_info_by_pack_size[2]), pricing.fba_strategy
		)

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 1})
	def test_fba_sibling_out_of_amazon_stock_falls_back_to_warehouse_packs(self):
		item_code, proxy_sku_by_pack_size = create_pack_pool([2, 3], self.template_name)
		make_fba(proxy_sku_by_pack_size[2], afn_stock=0)

		with patch(FREE_QTY_TARGET, return_value={item_code: {"free_qty": 21, "actual_qty": 21}}):
			stock_info_by_pack_size = {
				pack_size: get_stock_info(proxy_sku)
				for pack_size, proxy_sku in proxy_sku_by_pack_size.items()
			}

		# Amazon has run dry, so the pack of 2 ships off our shelves and shares the 21 units:
		# four rounds of 2 + 3 costs 20, and the odd unit buys nothing.
		self.assertEqual(stock_info_by_pack_size[2]["total_stock"], 4)
		self.assertEqual(stock_info_by_pack_size[3]["total_stock"], 4)

		# Warehouse stock beats the supplier, so it prices from Prime-from-warehouse, not backorder.
		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": proxy_sku_by_pack_size[2]})
		self.assertEqual(
			select_strategy(pricing, stock_info=stock_info_by_pack_size[2]), pricing.sfp_strategy
		)

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 1})
	def test_feed_ships_the_fallback_off_our_own_channel(self):
		item_code, proxy_sku_by_pack_size = create_pack_pool([2, 3], self.template_name)
		make_fba(proxy_sku_by_pack_size[2], afn_stock=0)

		pricing = frappe.get_doc("Amazon Product Pricing", {"proxy_sku": proxy_sku_by_pack_size[2]})
		pricing.active_strategy = pricing.sfp_strategy
		with patch(FREE_QTY_TARGET, return_value={item_code: {"free_qty": 21, "actual_qty": 21}}):
			inventory_data = get_sku_inventory_data(pricing)

		# The listing has to move to merchant-fulfilled for the warehouse packs to be sellable.
		self.assertEqual(inventory_data["fulfillment_channel"], "DEFAULT")
		self.assertFalse(inventory_data["is_fba"])
		self.assertEqual(inventory_data["quantity"], 4)

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 0})
	def test_sync_off_lets_the_fba_sibling_claim_packs(self):
		item_code, proxy_sku_by_pack_size = create_pack_pool([2, 3], self.template_name)
		# With the sync off afn_stock is never refreshed, so a stale non-zero value must not park
		# the SKU out of the pool forever — it is treated as out of FBA stock, as select_strategy does.
		make_fba(proxy_sku_by_pack_size[2], afn_stock=5)

		with patch(FREE_QTY_TARGET, return_value={item_code: {"free_qty": 21, "actual_qty": 21}}):
			stock_info_by_pack_size = {
				pack_size: get_stock_info(proxy_sku)
				for pack_size, proxy_sku in proxy_sku_by_pack_size.items()
			}

		self.assertEqual(stock_info_by_pack_size[2]["total_stock"], 4)
		self.assertEqual(stock_info_by_pack_size[3]["total_stock"], 4)

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 1})
	def test_pool_size_counts_the_fallen_back_fba_sibling(self):
		_item_code, proxy_sku_by_pack_size = create_pack_pool([2, 3], self.template_name)
		make_fba(proxy_sku_by_pack_size[2], afn_stock=0)

		# pool_size divides bundle and supplier quantities; it has to see the same claimants the
		# pack allocation does, or the two disagree about who is sharing the stock.
		info_map = build_proxy_sku_info_map(list(proxy_sku_by_pack_size.values()))
		self.assertEqual(info_map[proxy_sku_by_pack_size[2]]["pool_size"], 2)

		make_fba(proxy_sku_by_pack_size[2], afn_stock=5)
		info_map = build_proxy_sku_info_map(list(proxy_sku_by_pack_size.values()))
		self.assertEqual(info_map[proxy_sku_by_pack_size[3]]["pool_size"], 1)
