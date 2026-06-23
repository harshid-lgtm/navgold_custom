# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing import manual_price_push
from navgold_custom.navgold_custom.amazon_pricing.manual_price_push import (
	MAX_PRICE_RATIO,
	SANE_MAX_PRICE,
	push_manual_prices,
	validate_updates,
)

GOOD_ROW = {"sku": "TEST-SKU", "min": 8.86, "max": 12.0, "our_price": 9.5}


class TestManualPricePushValidation(IntegrationTestCase):
	def test_good_row_passes(self):
		validate_updates([GOOD_ROW])

	def test_non_positive_min_blocked(self):
		with self.assertRaises(frappe.ValidationError):
			validate_updates([{"sku": "T", "min": 0, "max": 12.0, "our_price": 5.0}])

	def test_non_positive_our_price_blocked(self):
		with self.assertRaises(frappe.ValidationError):
			validate_updates([{"sku": "T", "min": 5.0, "max": 12.0, "our_price": -1.0}])

	def test_min_above_max_blocked(self):
		with self.assertRaises(frappe.ValidationError):
			validate_updates([{"sku": "T", "min": 20.0, "max": 12.0, "our_price": 15.0}])

	def test_our_price_out_of_band_blocked(self):
		with self.assertRaises(frappe.ValidationError):
			validate_updates([{"sku": "T", "min": 5.0, "max": 12.0, "our_price": 99.0}])

	def test_above_sane_ceiling_blocked(self):
		over = SANE_MAX_PRICE + 1
		with self.assertRaises(frappe.ValidationError):
			validate_updates([{"sku": "T", "min": 5.0, "max": over, "our_price": 5.0}])

	def test_ratio_too_wide_blocked(self):
		# min=8.86 with the old default max=1000 is a ~113x band.
		with self.assertRaises(frappe.ValidationError):
			validate_updates([{"sku": "T", "min": 8.86, "max": 1000.0, "our_price": 8.86}])

	def test_ratio_at_limit_passes(self):
		# Exactly at the ceiling ratio (100x) must pass; only strictly above is blocked.
		validate_updates([{"sku": "T", "min": 1.0, "max": MAX_PRICE_RATIO, "our_price": 50.0}])

	def test_sub_cent_min_rounds_to_zero_blocked(self):
		# 0.004 is > 0 raw but rounds to 0.00 in the feed — must be caught.
		with self.assertRaises(frappe.ValidationError):
			validate_updates([{"sku": "T", "min": 0.004, "max": 12.0, "our_price": 9.0}])

	def test_missing_field_reported_distinctly(self):
		with self.assertRaisesRegex(frappe.ValidationError, "missing field"):
			validate_updates([{"sku": "T", "min": 5.0, "max": 12.0}])

	def test_blank_field_reported_as_missing_not_zero(self):
		with self.assertRaisesRegex(frappe.ValidationError, "missing field"):
			validate_updates([{"sku": "T", "min": "", "max": 12.0, "our_price": 9.0}])


class TestManualPricePushGate(IntegrationTestCase):
	def patched_push(self, **kwargs):
		# Patch every outbound seam so a stray live path cannot reach Amazon during tests.
		feeds_client = MagicMock()
		with (
			patch.object(manual_price_push, "get_feeds_client", return_value=feeds_client) as get_client,
			patch.object(manual_price_push, "build_feed", return_value=b"{}"),
			patch.object(manual_price_push, "sync_pricing_band", return_value=0),
			patch.object(frappe, "get_single", return_value=MagicMock()),
			patch.object(frappe, "new_doc", return_value=MagicMock()),
			patch.object(frappe.db, "commit"),
		):
			result = push_manual_prices([dict(GOOD_ROW)], **kwargs)
		return result, get_client, feeds_client

	def test_defaults_to_dry_run_and_submits_nothing(self):
		result, get_client, feeds_client = self.patched_push()
		self.assertTrue(result["dry_run"])
		get_client.assert_not_called()
		feeds_client.create_feed.assert_not_called()
		feeds_client.create_feed_document.assert_not_called()

	def test_going_live_requires_live_true(self):
		# live=True is the only way past the gate; verify it actually takes the submit path.
		result, get_client, feeds_client = self.patched_push(live=True)
		self.assertNotIn("dry_run", result)
		get_client.assert_called()
		feeds_client.create_feed.assert_called()

	def test_dry_run_param_no_longer_exists(self):
		# A leftover dry_run=False kwarg must NOT be accepted (gate is live-only now).
		with self.assertRaises(TypeError):
			push_manual_prices([dict(GOOD_ROW)], dry_run=False)


class TestManualPricePushBandSync(IntegrationTestCase):
	def test_sync_mirrors_pushed_band_for_matching_records_only(self):
		# Keeps the snapshot honest: only SKUs with a pricing record get their stored band
		# updated; an unknown CSV SKU is silently skipped (nothing to mirror).
		updates = [
			{"sku": "KNOWN-1", "min": 8.86, "max": 12.0, "our_price": 9.5},
			{"sku": "UNKNOWN-9", "min": 5.0, "max": 10.0, "our_price": 7.0},
		]
		with (
			patch.object(frappe, "get_all", return_value=["KNOWN-1"]),
			patch.object(frappe.db, "bulk_update") as bulk_update,
		):
			synced = manual_price_push.sync_pricing_band(updates)

		self.assertEqual(synced, 1)
		bulk_update.assert_called_once_with(
			"Amazon Product Pricing",
			{"KNOWN-1": {"current_min_price": 8.86, "current_max_price": 12.0}},
		)
