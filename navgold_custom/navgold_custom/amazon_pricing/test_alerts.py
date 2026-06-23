# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import MagicMock, patch

from frappe.tests import IntegrationTestCase

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


class TestActivityRollup(IntegrationTestCase):
	def test_rollup_shape(self):
		rollup = alerts.get_activity_rollup(30)
		for key in ("orders_synced", "prices_pushed", "repricer_pushed", "fba_proxy_count", "errors"):
			self.assertIsInstance(rollup[key], int)
		self.assertEqual(rollup["minutes"], 30)


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
		import frappe

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
