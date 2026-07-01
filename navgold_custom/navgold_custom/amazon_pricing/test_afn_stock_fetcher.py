# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing import afn_stock_fetcher
from navgold_custom.navgold_custom.amazon_pricing.afn_stock_fetcher import (
	compute_afn_stock_updates,
	compute_item_afn_totals,
	process_afn_stock_fetch,
)
from navgold_custom.navgold_custom.utils import ITEM_AFN_FIELD


class TestAFNStockFetcher(IntegrationTestCase):
	def test_compute_updates_present_sku_updated(self):
		current_rows = [{"name": "SKU-1", "afn_stock": 2}]
		value_map = {"SKU-1": 7.0}
		self.assertEqual(compute_afn_stock_updates(current_rows, value_map), {"SKU-1": {"afn_stock": 7.0}})

	def test_compute_updates_absent_sku_zeroed(self):
		# A row not present in a successful value_map is out of stock and must be zeroed.
		current_rows = [{"name": "SKU-1", "afn_stock": 5}]
		value_map = {"SKU-OTHER": 3.0}
		self.assertEqual(compute_afn_stock_updates(current_rows, value_map), {"SKU-1": {"afn_stock": 0.0}})

	def test_compute_updates_unchanged_value_omitted(self):
		current_rows = [{"name": "SKU-1", "afn_stock": 7}]
		value_map = {"SKU-1": 7.0}
		self.assertEqual(compute_afn_stock_updates(current_rows, value_map), {})

	def test_compute_updates_empty_map_zeroes_all_non_empty(self):
		current_rows = [
			{"name": "SKU-1", "afn_stock": 5},
			{"name": "SKU-2", "afn_stock": 0},
		]
		# SKU-2 already at 0 stays omitted; SKU-1 zeroed.
		self.assertEqual(compute_afn_stock_updates(current_rows, {}), {"SKU-1": {"afn_stock": 0.0}})

	def test_compute_updates_custom_field_name(self):
		current_rows = [{"name": "ITEM-1", "custom_amazon_afn_stock": 3}]
		value_map = {"ITEM-1": 12.0}
		self.assertEqual(
			compute_afn_stock_updates(current_rows, value_map, field="custom_amazon_afn_stock"),
			{"ITEM-1": {"custom_amazon_afn_stock": 12.0}},
		)

	def test_item_totals_two_skus_same_item_multiply_and_sum(self):
		proxy_rows = [
			{"name": "SKU-A", "item": "ITEM-1", "uom": "Box6"},
			{"name": "SKU-B", "item": "ITEM-1", "uom": "Nos"},
		]
		sku_qty_map = {"SKU-A": 2.0, "SKU-B": 5.0}
		factors = {("ITEM-1", "Box6"): 6.0, ("ITEM-1", "Nos"): 1.0}
		conversion = lambda item, uom: factors[(item, uom)]  # noqa: E731
		# 2 boxes of 6 = 12 base units + 5 singles = 17 total in stock UOM.
		self.assertEqual(compute_item_afn_totals(proxy_rows, sku_qty_map, conversion), {"ITEM-1": 17.0})

	def test_item_totals_missing_sku_contributes_zero(self):
		proxy_rows = [
			{"name": "SKU-A", "item": "ITEM-1", "uom": "Box6"},
			{"name": "SKU-B", "item": "ITEM-1", "uom": "Nos"},
		]
		# SKU-B not returned by Amazon → 0, only SKU-A contributes.
		sku_qty_map = {"SKU-A": 3.0}
		factors = {("ITEM-1", "Box6"): 6.0, ("ITEM-1", "Nos"): 1.0}
		conversion = lambda item, uom: factors[(item, uom)]  # noqa: E731
		self.assertEqual(compute_item_afn_totals(proxy_rows, sku_qty_map, conversion), {"ITEM-1": 18.0})

	def test_item_totals_all_skus_zero_yields_zero_total(self):
		proxy_rows = [{"name": "SKU-A", "item": "ITEM-1", "uom": "Box6"}]
		sku_qty_map = {"SKU-A": 0.0}
		conversion = lambda item, uom: 6.0  # noqa: E731
		# An FBA item whose SKUs all dropped to 0 must produce a 0 total so it gets zeroed.
		self.assertEqual(compute_item_afn_totals(proxy_rows, sku_qty_map, conversion), {"ITEM-1": 0.0})

	def test_item_totals_distinct_items_kept_separate(self):
		proxy_rows = [
			{"name": "SKU-A", "item": "ITEM-1", "uom": "Nos"},
			{"name": "SKU-B", "item": "ITEM-2", "uom": "Nos"},
		]
		sku_qty_map = {"SKU-A": 4.0, "SKU-B": 9.0}
		conversion = lambda item, uom: 1.0  # noqa: E731
		self.assertEqual(
			compute_item_afn_totals(proxy_rows, sku_qty_map, conversion),
			{"ITEM-1": 4.0, "ITEM-2": 9.0},
		)


def create_item(item_code, afn_stock=0):
	if not frappe.db.exists("Item", item_code):
		item = frappe.new_doc("Item")
		item.item_code = item_code
		item.item_name = item_code
		item.item_group = "All Item Groups"
		item.stock_uom = "Nos"
		item.is_stock_item = 0
		item.insert()
	frappe.db.set_value("Item", item_code, ITEM_AFN_FIELD, afn_stock)
	return item_code


def create_fba_proxy_sku(item_code, afn_stock=0, is_active=1):
	proxy = frappe.new_doc("Proxy SKU")
	proxy.item = item_code
	proxy.uom = "Nos"
	proxy.is_fba = 1
	proxy.is_active = is_active
	proxy.afn_stock = afn_stock
	proxy.insert()
	return proxy.name


class TestProcessAFNStockFetch(IntegrationTestCase):
	# The orchestrator's stock-safety invariant: a failed Amazon pull must never zero real
	# stock (oversell risk on a live-money system), and a good pull must persist stock.
	FBA_PATH = "navgold_custom.navgold_custom.amazon_pricing.afn_stock_fetcher.FBAInventory"
	NOTIFY_PATH = "navgold_custom.navgold_custom.amazon_pricing.alerts.notify"
	# save_last_report_attachment deletes/writes real files on disk (delete_permanently is not
	# transactional) — patch it so this test cannot corrupt the dev site's last-report attachment.
	SAVE_ATTACHMENT_PATH = (
		"navgold_custom.navgold_custom.amazon_pricing.afn_stock_fetcher.save_last_report_attachment"
	)

	def setUp(self):
		super().setUp()
		suffix = frappe.generate_hash(length=8)
		self.item_code = f"AFN-ITEM-{suffix}"
		create_item(self.item_code, afn_stock=50)
		self.proxy_name = create_fba_proxy_sku(self.item_code, afn_stock=50)

		# An item that previously held FBA stock but has NO active FBA proxy contributing — its
		# last FBA proxy was deactivated, so a successful pull must zero it (~lines 77-83).
		self.orphan_item_code = f"AFN-ORPHAN-{suffix}"
		create_item(self.orphan_item_code, afn_stock=30)
		create_fba_proxy_sku(self.orphan_item_code, afn_stock=30, is_active=0)

		# Clear the sync stamp so asserting it is set proves THIS run stamped it (not a prior one).
		frappe.db.set_single_value("Navgold Settings", "last_afn_stock_synced_at", None)

	def test_fetch_failure_does_not_zero_stock(self):
		fba = MagicMock()
		fba.from_amazon_settings.return_value.fetch_for_skus.side_effect = RuntimeError("Amazon 503")

		with patch(self.FBA_PATH, fba), patch(self.NOTIFY_PATH) as mocked_notify:
			process_afn_stock_fetch()

		# The critical safety invariant: existing stock is UNTOUCHED after a failed pull.
		self.assertEqual(
			frappe.db.get_value("Proxy SKU", self.proxy_name, "afn_stock"),
			50,
			"Failed FBA pull must NOT zero Proxy SKU stock (oversell risk)",
		)
		self.assertEqual(
			frappe.db.get_value("Item", self.item_code, ITEM_AFN_FIELD),
			50,
			"Failed FBA pull must NOT zero Item AFN stock (oversell risk)",
		)

		mocked_notify.assert_called_once()
		self.assertEqual(mocked_notify.call_args.kwargs.get("dedupe_key"), "fba_pull_error")

	def test_successful_fetch_persists_stock_and_zeroes_orphan(self):
		fba = MagicMock()
		fba.from_amazon_settings.return_value.fetch_for_skus.return_value = {
			self.proxy_name: {"fulfillableQuantity": 7}
		}

		# commit() would escape the test transaction and mutate real site AFN stock — neutralize it
		# so rollback still isolates this test; assertions read the in-transaction persisted rows.
		with (
			patch(self.FBA_PATH, fba),
			patch(self.SAVE_ATTACHMENT_PATH),
			patch.object(frappe.db, "commit"),
		):
			process_afn_stock_fetch()

		# Proxy SKU stock persisted from the Amazon payload via the real update path.
		self.assertEqual(frappe.db.get_value("Proxy SKU", self.proxy_name, "afn_stock"), 7)
		# Item AFN persisted (UOM "Nos" -> conversion factor 1, so 7 base units).
		self.assertEqual(frappe.db.get_value("Item", self.item_code, ITEM_AFN_FIELD), 7)
		# Orphaned item (last active FBA proxy deactivated) zeroed.
		self.assertEqual(frappe.db.get_value("Item", self.orphan_item_code, ITEM_AFN_FIELD), 0)
		# Sync timestamp stamped on the Single.
		self.assertIsNotNone(frappe.db.get_single_value("Navgold Settings", "last_afn_stock_synced_at"))
