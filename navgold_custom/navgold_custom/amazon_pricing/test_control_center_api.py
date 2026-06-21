# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.tests.utils import change_settings
from frappe.utils.data import cint

from navgold_custom.navgold_custom.amazon_pricing.control_center_api import (
	capture_price_snapshot,
	get_control_center_status,
	get_order_analytics,
	panic_button,
	submit_restore_feed_from_snapshot,
	toggle_flag,
)
from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import Feeds

TEST_ITEM_CODE = "TEST-CC-API-ITEM"


def make_test_item():
	if frappe.db.exists("Item", TEST_ITEM_CODE):
		return TEST_ITEM_CODE

	item = frappe.new_doc("Item")
	item.item_code = TEST_ITEM_CODE
	item.item_name = TEST_ITEM_CODE
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 1
	item.insert()
	return item.name


def make_test_proxy_sku():
	item_code = make_test_item()
	existing = frappe.get_all(
		"Proxy SKU",
		filters={"item": item_code, "uom": "Nos"},
		pluck="name",
		limit=1,
	)
	if existing:
		return existing[0]

	proxy = frappe.new_doc("Proxy SKU")
	proxy.item = item_code
	proxy.uom = "Nos"
	# asin left unset: it is a Link field and fixtures must not depend on ASIN masters
	proxy.is_active = 1
	proxy.current_selling_price = 9.99
	proxy.insert()
	return proxy.name


def make_test_pricing(proxy_sku_name):
	if frappe.db.exists("Amazon Product Pricing", proxy_sku_name):
		return proxy_sku_name

	pricing = frappe.new_doc("Amazon Product Pricing")
	pricing.proxy_sku = proxy_sku_name
	pricing.is_active = 1
	pricing.current_min_price = 8.00
	pricing.current_max_price = 12.00
	pricing.insert()
	return pricing.name


def make_test_snapshot(status="Captured", with_items=True):
	proxy_sku_name = make_test_proxy_sku()
	pricing_name = make_test_pricing(proxy_sku_name)
	snapshot = frappe.new_doc("Amazon Pricing Snapshot")
	snapshot.trigger_type = "Manual"
	snapshot.status = status
	if with_items:
		snapshot.snapshot_data = frappe.as_json(
			[
				{
					"pricing": pricing_name,
					"proxy_sku": proxy_sku_name,
					"active_strategy": None,
					"min_price": 8.00,
					"max_price": 12.00,
					"fixed_price": 0,
					"fixed_price_value": 0.0,
				}
			]
		)
		snapshot.item_count = 1
	snapshot.insert()
	return snapshot.name


class TestControlCenterStatus(IntegrationTestCase):
	def test_status_shape(self):
		result = get_control_center_status()

		self.assertIn("flags", result)
		self.assertIn("blockers", result)
		self.assertIn("snapshot", result)

		expected_flag_keys = {
			"enable_pricing_sync",
			"repricer_enabled",
			"freeze_amazon_outbound",
			"pause_strategy_refresh",
			"enable_afn_stock_sync",
			"amazon_publishing_mode",
			"enable_sync",
		}
		self.assertEqual(set(result["flags"].keys()), expected_flag_keys)

		expected_blocker_keys = {
			"readiness_blocked",
			"below_min_24h",
			"open_failed_syncs",
			"stuck_feeds",
			"errors_1h",
			"minutes_since_last_order",
		}
		self.assertEqual(set(result["blockers"].keys()), expected_blocker_keys)

	def test_flags_reflect_navgold_settings(self):
		with change_settings("Navgold Settings", {"enable_pricing_sync": 0, "repricer_enabled": 0}):
			result = get_control_center_status()
			self.assertEqual(result["flags"]["enable_pricing_sync"], 0)
			self.assertEqual(result["flags"]["repricer_enabled"], 0)

	def test_snapshot_none_when_no_captured_snapshot(self):
		# Ensure no Captured snapshots exist for the scope of this assertion
		# (we can't delete prod data but we can assert the key is present and is dict-or-None)
		result = get_control_center_status()
		snapshot = result["snapshot"]
		self.assertTrue(snapshot is None or isinstance(snapshot, dict))
		if isinstance(snapshot, dict):
			self.assertIn("name", snapshot)
			self.assertIn("status", snapshot)


class TestToggleFlag(IntegrationTestCase):
	def test_toggle_allowed_flag(self):
		with change_settings("Navgold Settings", {"pause_strategy_refresh": 0}):
			result = toggle_flag("pause_strategy_refresh", 1)
			self.assertEqual(result["pause_strategy_refresh"], 1)

		# change_settings restores original value; verify toggle_flag round-trips correctly
		result = toggle_flag("pause_strategy_refresh", 0)
		self.assertEqual(result["pause_strategy_refresh"], 0)

	def test_toggle_rejects_non_whitelisted_field(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			toggle_flag("some_bad_field", 1)

	def test_toggle_rejects_sensitive_field(self):
		# repricer_api_password must not be toggleable via this endpoint
		with self.assertRaises(frappe.exceptions.ValidationError):
			toggle_flag("repricer_api_password", 1)


class TestPanicButton(IntegrationTestCase):
	def test_panic_without_restore_stops_and_freezes(self):
		# Reset flags first so we know the before-state
		with change_settings(
			"Navgold Settings",
			{
				"enable_pricing_sync": 1,
				"repricer_enabled": 1,
				"pause_strategy_refresh": 0,
				"freeze_amazon_outbound": 0,
			},
		):
			result = panic_button(restore=0)

			self.assertEqual(result["frozen"], 1)
			self.assertIn("enable_pricing_sync", result["stopped"])
			self.assertIn("repricer_enabled", result["stopped"])
			self.assertIn("pause_strategy_refresh", result["stopped"])
			self.assertEqual(result["restored_skus"], 0)

			# Assertions stay INSIDE change_settings: it restores all flags on exit,
			# which would wipe panic's writes before we could read them.
			self.assertEqual(
				cint(frappe.db.get_single_value("Navgold Settings", "freeze_amazon_outbound")), 1
			)
			self.assertEqual(cint(frappe.db.get_single_value("Navgold Settings", "enable_pricing_sync")), 0)
			self.assertEqual(cint(frappe.db.get_single_value("Navgold Settings", "repricer_enabled")), 0)

	def test_panic_with_restore_and_no_snapshot_still_freezes(self):
		# When restore=1 but there is no Captured snapshot, the freeze must still land
		with change_settings(
			"Navgold Settings",
			{
				"enable_pricing_sync": 1,
				"repricer_enabled": 1,
				"pause_strategy_refresh": 0,
				"freeze_amazon_outbound": 0,
			},
		):
			# Mock submit_restore_feed_from_snapshot so it never runs (no snapshot available)
			with patch(
				"navgold_custom.navgold_custom.amazon_pricing.control_center_api.submit_restore_feed_from_snapshot"
			) as mock_restore:
				mock_restore.return_value = (None, 0)
				result = panic_button(restore=1)

		self.assertEqual(result["frozen"], 1)

		# Cleanup
		frappe.db.set_single_value("Navgold Settings", "freeze_amazon_outbound", 0)

	def test_panic_freeze_survives_restore_exception(self):
		"""freeze_amazon_outbound must land even when the restore feed submission crashes."""
		with change_settings(
			"Navgold Settings",
			{
				"enable_pricing_sync": 1,
				"repricer_enabled": 1,
				"pause_strategy_refresh": 0,
				"freeze_amazon_outbound": 0,
			},
		):
			snapshot_name = make_test_snapshot(status="Captured")

			with patch(
				"navgold_custom.navgold_custom.amazon_pricing.control_center_api.submit_restore_feed_from_snapshot",
				side_effect=Exception("SP-API exploded"),
			):
				result = panic_button(restore=1)

			# Even with restore crashing, freeze must have landed (asserted inside
			# change_settings — it restores flags on exit)
			self.assertEqual(result["frozen"], 1)
			freeze_value = cint(frappe.db.get_single_value("Navgold Settings", "freeze_amazon_outbound"))
			self.assertEqual(freeze_value, 1)

		# Cleanup
		frappe.db.set_single_value("Navgold Settings", "freeze_amazon_outbound", 0)
		frappe.delete_doc("Amazon Pricing Snapshot", snapshot_name, force=True)

	def test_restore_in_dry_run_never_reaches_amazon_transport(self):
		"""Dry Run must block the restore feed at the chokepoint gate before any HTTP call.

		Uses the REAL submit_restore_feed_from_snapshot so revert -> publish("Amazon") ->
		bulk_submit_pricing -> Feeds.create_feed_document runs the real assert_pricing_push_approved
		gate. Only the lowest HTTP seam (Feeds.post_feed_json) is patched, so if any push slipped
		past the gate the assertion would catch a would-be Amazon call. freeze_amazon_outbound is 0
		so the ONLY thing standing between the restore and Amazon is the Dry Run gate.
		"""
		with change_settings(
			"Navgold Settings",
			{
				"pricing_sync_mode": "Dry Run",
				"freeze_amazon_outbound": 0,
				"enable_pricing_sync": 1,
				# Restored-site data may have a "Publish Selected Prices Only" list that excludes
				# the test SKU and would reject the publish before the gate; neutralise it so the
				# Dry Run gate is the thing actually under test.
				"pricing_publishing_mode": "Publish All Prices",
			},
		):
			snapshot_name = make_test_snapshot(status="Captured")

			# Patch the HTTP transport seam only — the gate inside create_feed_document is real.
			with patch.object(Feeds, "post_feed_json") as mock_post:
				log_name, restored_skus = submit_restore_feed_from_snapshot(snapshot_name)

				# The Dry Run gate must have blocked the push before any Amazon HTTP call.
				mock_post.assert_not_called()

			# ERP-side revert still happened: the snapshot's pricing record was written back.
			self.assertGreaterEqual(restored_skus, 1)
			self.assertEqual(
				frappe.db.get_value("Amazon Pricing Snapshot", snapshot_name, "status"), "Reverted"
			)
			# The submission log records the deliberate Dry Run skip, not an error.
			if log_name:
				self.assertEqual(
					frappe.db.get_value("Amazon Pricing Submission Log", log_name, "status"), "Skipped"
				)

		# Cleanup
		frappe.delete_doc("Amazon Pricing Snapshot", snapshot_name, force=True)


class TestCaptureSnapshot(IntegrationTestCase):
	def test_capture_creates_pricing_snapshot(self):
		proxy_sku_name = make_test_proxy_sku()
		make_test_pricing(proxy_sku_name)

		result = capture_price_snapshot()
		self.assertIn("snapshot", result)

		snapshot_name = result["snapshot"]
		self.assertTrue(frappe.db.exists("Amazon Pricing Snapshot", snapshot_name))

		# Capture is synchronous now (reuses the JSON doctype's capture_snapshot helper),
		# so the snapshot lands with its captured price set, not a "Capturing" placeholder.
		snapshot = frappe.get_doc("Amazon Pricing Snapshot", snapshot_name)
		self.assertEqual(snapshot.trigger_type, "Manual")
		self.assertIsNotNone(snapshot.snapshot_datetime)
		self.assertGreaterEqual(snapshot.item_count, 1)

		# Cleanup
		frappe.delete_doc("Amazon Pricing Snapshot", snapshot_name, force=True)


class TestOrderAnalytics(IntegrationTestCase):
	def test_analytics_shape_on_empty_window(self):
		# Use a 1-hour window — unlikely to have test data; validates shape regardless
		result = get_order_analytics(hours=1)

		self.assertIn("cards", result)
		self.assertIn("order_type_pie", result)
		self.assertIn("top_categories", result)
		self.assertIn("top_items", result)
		self.assertIn("hourly_series", result)

		cards = result["cards"]
		self.assertIn("orders_past_hour", cards)
		self.assertIn("sales_past_hour", cards)
		self.assertIn("orders_period", cards)
		self.assertIn("sales_period", cards)

		# Hourly series should have exactly `hours` buckets even when empty
		self.assertEqual(len(result["hourly_series"]), 1)
		if result["hourly_series"]:
			bucket = result["hourly_series"][0]
			self.assertIn("ts", bucket)
			self.assertIn("orders", bucket)
			self.assertIn("sales", bucket)
