# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

"""Scheduled pricing sync respects pricing_sync_mode: Dry Run renders the preview feed via
dry_run_pricing and never submits live; Live submits for real."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, change_settings
from frappe.utils import add_to_date, now_datetime

from navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings import (
	schedule_submit_active_pricing,
	schedule_submit_active_repricer,
)

DRY_RUN_METHOD = "navgold_custom.navgold_custom.amazon_pricing.pricing_dry_run.dry_run_pricing"
LIVE_PRICING_METHOD = (
	"navgold_custom.navgold_custom.amazon_pricing.amazon_feed_submitter.submit_active_pricing"
)
REPRICER_METHOD = "navgold_custom.navgold_custom.amazon_pricing.repricer_client.submit_active_repricer"


def enqueued_methods(mock_enqueue):
	return [call.kwargs.get("method") for call in mock_enqueue.call_args_list]


class TestDryRunSchedule(IntegrationTestCase):
	def make_settings(self, mode):
		now = now_datetime()
		# A refresh completed after the last submit window so the stagger guard does not defer.
		return change_settings(
			"Navgold Settings",
			{
				"enable_pricing_sync": 1,
				"amazon_pricing_enabled": 1,
				"repricer_enabled": 1,
				"pricing_sync_mode": mode,
				"last_strategy_refresh_at": add_to_date(now, minutes=-5),
				"last_pricing_submit_at": add_to_date(now, minutes=-20),
				"last_repricer_submit_at": add_to_date(now, minutes=-20),
			},
		)

	def test_dry_run_mode_renders_preview_not_live_submit(self):
		with self.make_settings("Dry Run"):
			frappe.clear_document_cache("Navgold Settings", "Navgold Settings")
			with patch("frappe.enqueue") as mock_enqueue:
				schedule_submit_active_pricing()
			methods = enqueued_methods(mock_enqueue)
			self.assertIn(DRY_RUN_METHOD, methods)
			self.assertNotIn(LIVE_PRICING_METHOD, methods)

	def test_live_mode_submits_for_real_not_dry_run(self):
		with self.make_settings("Live"):
			frappe.clear_document_cache("Navgold Settings", "Navgold Settings")
			with patch("frappe.enqueue") as mock_enqueue:
				schedule_submit_active_pricing()
			methods = enqueued_methods(mock_enqueue)
			self.assertIn(LIVE_PRICING_METHOD, methods)
			self.assertNotIn(DRY_RUN_METHOD, methods)

	def test_dry_run_mode_skips_repricer_submit(self):
		with self.make_settings("Dry Run"):
			frappe.clear_document_cache("Navgold Settings", "Navgold Settings")
			with patch("frappe.enqueue") as mock_enqueue:
				schedule_submit_active_repricer()
			self.assertNotIn(REPRICER_METHOD, enqueued_methods(mock_enqueue))

	def test_live_mode_enqueues_repricer_submit(self):
		with self.make_settings("Live"):
			frappe.clear_document_cache("Navgold Settings", "Navgold Settings")
			with patch("frappe.enqueue") as mock_enqueue:
				schedule_submit_active_repricer()
			self.assertIn(REPRICER_METHOD, enqueued_methods(mock_enqueue))
