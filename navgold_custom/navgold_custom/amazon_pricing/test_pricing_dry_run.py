# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing import pricing_dry_run
from navgold_custom.navgold_custom.amazon_pricing.pricing_dry_run import (
	calculate_percent_change,
	compute_price_variance,
)


class TestDeltaPct(IntegrationTestCase):
	def test_zero_previous_value_does_not_divide_by_zero(self):
		self.assertEqual(calculate_percent_change(0, 10), 0.0)
		self.assertEqual(calculate_percent_change(None, 10), 0.0)

	def test_normal_delta(self):
		self.assertEqual(calculate_percent_change(10, 12), 20.0)
		self.assertEqual(calculate_percent_change(10, 8), -20.0)


class TestComputePriceVariance(IntegrationTestCase):
	def run_variance(self, current_rows, previous_rows, threshold):
		previous_snapshot = (
			frappe._dict(name="PREV-1", snapshot_data=frappe.as_json(previous_rows))
			if previous_rows is not None
			else None
		)
		with (
			patch.object(pricing_dry_run, "get_previous_snapshot", return_value=previous_snapshot),
			patch.object(pricing_dry_run, "get_price_change_threshold", return_value=threshold),
		):
			return compute_price_variance(current_rows)

	def test_threshold_flag_marks_movers(self):
		current = [{"pricing": "P1", "proxy_sku": "S1", "min_price": 12.0, "max_price": 20.0}]
		previous = [{"pricing": "P1", "proxy_sku": "S1", "min_price": 10.0, "max_price": 20.0}]
		# min moved +20%, threshold 10% → flagged
		result = self.run_variance(current, previous, threshold=10.0)
		self.assertEqual(result["summary"]["movers"], 1)
		self.assertEqual(result["rows"][0]["status"], "Flagged")
		self.assertEqual(result["rows"][0]["min_delta_pct"], 20.0)

	def test_within_threshold_is_ok(self):
		current = [{"pricing": "P1", "proxy_sku": "S1", "min_price": 10.5, "max_price": 20.0}]
		previous = [{"pricing": "P1", "proxy_sku": "S1", "min_price": 10.0, "max_price": 20.0}]
		# min moved +5%, threshold 10% → OK
		result = self.run_variance(current, previous, threshold=10.0)
		self.assertEqual(result["summary"]["movers"], 0)
		self.assertEqual(result["rows"][0]["status"], "OK")

	def test_new_sku_counted_as_new_not_mover(self):
		current = [{"pricing": "P2", "proxy_sku": "S2", "min_price": 5.0, "max_price": 9.0}]
		previous = [{"pricing": "P1", "proxy_sku": "S1", "min_price": 10.0, "max_price": 20.0}]
		result = self.run_variance(current, previous, threshold=10.0)
		self.assertEqual(result["summary"]["new"], 1)
		self.assertEqual(result["summary"]["movers"], 0)
		self.assertEqual(result["rows"][0]["status"], "New")
		self.assertIsNone(result["rows"][0]["prev_min"])

	def test_dropped_sku_detected(self):
		current = [{"pricing": "P1", "proxy_sku": "S1", "min_price": 10.0, "max_price": 20.0}]
		previous = [
			{"pricing": "P1", "proxy_sku": "S1", "min_price": 10.0, "max_price": 20.0},
			{"pricing": "P9", "proxy_sku": "S9", "min_price": 3.0, "max_price": 6.0},
		]
		result = self.run_variance(current, previous, threshold=10.0)
		self.assertEqual(result["summary"]["dropped"], 1)
		self.assertEqual(result["dropped"][0]["pricing"], "P9")

	def test_no_previous_snapshot_all_new(self):
		current = [{"pricing": "P1", "proxy_sku": "S1", "min_price": 10.0, "max_price": 20.0}]
		result = self.run_variance(current, previous_rows=None, threshold=10.0)
		self.assertEqual(result["summary"]["new"], 1)
		self.assertEqual(result["summary"]["dropped"], 0)
		self.assertIsNone(result["summary"]["compared_against"])
