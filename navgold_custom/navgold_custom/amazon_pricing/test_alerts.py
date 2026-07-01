# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.data import add_days, cint, nowdate

from navgold_custom.navgold_custom.amazon_pricing import alerts

READY_CONFIG = {"enabled": 1, "token": "tok", "chat_id": "123"}


class TestTelegramSeverity(IntegrationTestCase):
	def sent_payload(self, severity):
		with (
			patch.object(alerts, "get_alert_config", return_value=dict(READY_CONFIG)),
			patch.object(alerts, "make_post_request") as post,
		):
			alerts.notify("Title", ["line one"], severity=severity)
		self.assertTrue(post.called)
		return post.call_args.kwargs["data"]

	def test_critical_pings_and_is_prefixed(self):
		data = self.sent_payload("critical")
		self.assertEqual(data["disable_notification"], "false")
		self.assertTrue(data["text"].startswith("🔴 CRITICAL — "))

	def test_warning_pings_and_is_prefixed(self):
		data = self.sent_payload("warning")
		self.assertEqual(data["disable_notification"], "false")
		self.assertTrue(data["text"].startswith("🟡 WARNING — "))

	def test_info_is_silent_and_colour_keyed(self):
		data = self.sent_payload("info")
		self.assertEqual(data["disable_notification"], "true")
		self.assertTrue(data["text"].startswith("🔵 "))

	def test_ok_is_silent_and_green(self):
		data = self.sent_payload("ok")
		self.assertEqual(data["disable_notification"], "true")
		self.assertTrue(data["text"].startswith("🟢 "))

	def test_notify_noop_when_channel_unconfigured(self):
		with (
			patch.object(alerts, "get_alert_config", return_value={"enabled": 0}),
			patch.object(alerts, "make_post_request") as post,
		):
			alerts.notify("Title", ["x"], severity="critical")
		self.assertFalse(post.called)


class NavgoldAlertsTestBase(IntegrationTestCase):
	# Circuit-breaker window is a shared Redis sliding buffer: isolate every test.
	def setUp(self):
		frappe.cache().delete_value(alerts.LOSS_WINDOW_CACHE_KEY)

	def tearDown(self):
		frappe.cache().delete_value(alerts.LOSS_WINDOW_CACHE_KEY)

	def create_test_stock_item(self):
		suffix = frappe.generate_hash(length=8)
		item = frappe.new_doc("Item")
		item.item_code = f"ALERT-CB-{suffix}"
		item.item_name = item.item_code
		item.item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
		item.stock_uom = "Nos"
		item.is_stock_item = 1
		item.insert(ignore_permissions=True)
		return item.item_code

	def create_below_min_pricing(self, min_price=10.0):
		item_code = self.create_test_stock_item()
		proxy = frappe.new_doc("Proxy SKU")
		proxy.item = item_code
		proxy.uom = "Nos"
		proxy.is_active = 1
		proxy.insert(ignore_permissions=True)

		pricing = frappe.new_doc("Amazon Product Pricing")
		pricing.proxy_sku = proxy.name
		pricing.is_active = 1
		pricing.fixed_price = 1
		pricing.fixed_price_value = min_price
		pricing.insert(ignore_permissions=True)
		return item_code

	def create_amazon_sales_order(self, item_code, rate, qty=1, submit=False):
		warehouse = frappe.db.get_value("Warehouse", {"is_group": 0, "disabled": 0}, "name")
		so = frappe.new_doc("Sales Order")
		so.customer = frappe.db.get_value("Customer", {"disabled": 0}, "name")
		so.company = frappe.db.get_value("Company", {}, "name")
		so.transaction_date = nowdate()
		so.delivery_date = add_days(nowdate(), 5)
		so.amazon_order_id = f"AMZ-{frappe.generate_hash(length=10)}"
		so.append(
			"items",
			{
				"item_code": item_code,
				"qty": qty,
				"rate": rate,
				"uom": "Nos",
				"warehouse": warehouse,
				"delivery_date": add_days(nowdate(), 5),
			},
		)
		so.insert(ignore_permissions=True)
		if submit:
			so.submit()
		return so


class TestCircuitBreaker(NavgoldAlertsTestBase):
	# Below-min loss 9 (min 10, rate 1, qty 1) > threshold 5 => kill-switch trips.
	def test_below_min_loss_over_threshold_persists_freeze(self):
		item_code = self.create_below_min_pricing(min_price=10)
		sales_order = self.create_amazon_sales_order(item_code, rate=1, qty=1)

		with self.change_settings(
			"Navgold Settings",
			{
				"enable_loss_circuit_breaker": 1,
				"loss_circuit_breaker_threshold": 5,
				"freeze_amazon_outbound": 0,
				"enable_telegram_alerts": 0,
			},
		):
			alerts.check_sales_order_below_min(sales_order)
			self.assertEqual(
				cint(frappe.db.get_single_value("Navgold Settings", "freeze_amazon_outbound")), 1
			)

	# Loss 9 < threshold 100 => must stay unfrozen.
	def test_under_threshold_loss_leaves_freeze_off(self):
		item_code = self.create_below_min_pricing(min_price=10)
		sales_order = self.create_amazon_sales_order(item_code, rate=1, qty=1)

		with self.change_settings(
			"Navgold Settings",
			{
				"enable_loss_circuit_breaker": 1,
				"loss_circuit_breaker_threshold": 100,
				"freeze_amazon_outbound": 0,
				"enable_telegram_alerts": 0,
			},
		):
			alerts.check_sales_order_below_min(sales_order)
			self.assertEqual(
				cint(frappe.db.get_single_value("Navgold Settings", "freeze_amazon_outbound")), 0
			)

	# Master switch OFF: even a loss far above threshold must never freeze.
	def test_disabled_breaker_never_trips(self):
		with self.change_settings(
			"Navgold Settings",
			{
				"enable_loss_circuit_breaker": 0,
				"loss_circuit_breaker_threshold": 5,
				"freeze_amazon_outbound": 0,
			},
		):
			tripped = alerts.trip_circuit_breaker_if_needed(1000)
			self.assertFalse(tripped)
			self.assertEqual(
				cint(frappe.db.get_single_value("Navgold Settings", "freeze_amazon_outbound")), 0
			)

	# Already frozen: guard short-circuits, no re-trip.
	def test_already_frozen_is_idempotent(self):
		with self.change_settings(
			"Navgold Settings",
			{
				"enable_loss_circuit_breaker": 1,
				"loss_circuit_breaker_threshold": 5,
				"freeze_amazon_outbound": 1,
				"enable_telegram_alerts": 0,
			},
		):
			tripped = alerts.trip_circuit_breaker_if_needed(1000)
			self.assertFalse(tripped)
			self.assertEqual(
				cint(frappe.db.get_single_value("Navgold Settings", "freeze_amazon_outbound")), 1
			)

	# Scheduled sweep re-reads submitted below-min sales and trips independently.
	def test_sweep_trips_on_recent_below_min_sale(self):
		item_code = self.create_below_min_pricing(min_price=10)
		# Submitted while the breaker is disabled (site default), so on_submit does not pre-trip.
		self.create_amazon_sales_order(item_code, rate=1, qty=1, submit=True)

		with self.change_settings(
			"Navgold Settings",
			{
				"enable_loss_circuit_breaker": 1,
				"loss_circuit_breaker_threshold": 5,
				"freeze_amazon_outbound": 0,
				"enable_telegram_alerts": 0,
			},
		):
			alerts.sweep_circuit_breaker()
			self.assertEqual(
				cint(frappe.db.get_single_value("Navgold Settings", "freeze_amazon_outbound")), 1
			)

	# Sweep must be a no-op once already frozen (guard at top of sweep).
	def test_sweep_noop_when_already_frozen(self):
		with self.change_settings(
			"Navgold Settings",
			{
				"enable_loss_circuit_breaker": 1,
				"loss_circuit_breaker_threshold": 5,
				"freeze_amazon_outbound": 1,
				"enable_telegram_alerts": 0,
			},
		):
			alerts.sweep_circuit_breaker()
			self.assertEqual(
				cint(frappe.db.get_single_value("Navgold Settings", "freeze_amazon_outbound")), 1
			)


class TestActivityRollup(NavgoldAlertsTestBase):
	# Real counts, not shape: one in-window amazon order among thousands of old ones
	# proves the creation cutoff; an Error log + a Failed sync are both counted as errors.
	def test_rollup_counts_in_window_activity(self):
		item_code = self.create_test_stock_item()
		self.create_amazon_sales_order(item_code, rate=50, qty=1)

		error_log = frappe.new_doc("Amazon Pricing Submission Log")
		error_log.submission_type = "Amazon"
		error_log.status = "Error"
		error_log.insert(ignore_permissions=True)

		failed_sync = frappe.new_doc("Amazon Failed Sync Record")
		failed_sync.amazon_order_id = frappe.generate_hash(length=12)
		failed_sync.status = "Failed"
		failed_sync.insert(ignore_permissions=True)

		rollup = alerts.get_activity_rollup(30)
		self.assertEqual(rollup["minutes"], 30)
		self.assertEqual(rollup["orders_synced"], 1)
		self.assertEqual(rollup["errors"], 2)


class TestDigest(IntegrationTestCase):
	def test_digest_includes_activity_line(self):
		control_data = {
			"readiness": {"blocked_total": 0, "ok_total": 5, "total_active": 5},
			"below_min": {"count": 0, "estimated_loss": 0, "velocity": {}},
			"feeds": {"status_counts": {}},
			"orders": {"summary": []},
		}
		with (
			patch.object(alerts, "get_alert_config", return_value=dict(READY_CONFIG)),
			patch(
				"navgold_custom.navgold_custom.amazon_pricing.control_center.get_control_center_data",
				return_value=control_data,
			),
			patch.object(alerts, "make_post_request") as post,
		):
			alerts.send_digest()
		self.assertTrue(post.called)
		self.assertIn("Activity (30m):", post.call_args.kwargs["data"]["text"])


class TestAlertConfig(IntegrationTestCase):
	def test_config_reads_navgold_settings_fields(self):
		settings = frappe.get_doc("Navgold Settings")
		settings.enable_telegram_alerts = 1
		settings.telegram_bot_token = "secret-token"
		settings.telegram_chat_id = "999"
		settings.save()
		frappe.clear_cache(doctype="Navgold Settings")

		config = alerts.get_alert_config()
		self.assertEqual(config["enabled"], 1)
		self.assertEqual(config["token"], "secret-token")
		self.assertEqual(config["chat_id"], "999")
