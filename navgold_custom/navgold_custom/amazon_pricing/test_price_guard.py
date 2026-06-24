# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from contextlib import contextmanager
from unittest.mock import patch

from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing import price_guard

SETTINGS_FIELD_TO_KEY = {
	"price_change_threshold_percent": "threshold",
	"price_change_block_direction": "direction",
}


@contextmanager
def mock_settings(threshold, direction, selling_prices):
	settings = {"threshold": threshold, "direction": direction}

	def fake_get_cached_value(doctype, name, fieldname):
		return settings[SETTINGS_FIELD_TO_KEY[fieldname]]

	def fake_get_all(doctype, filters=None, fields=None, **kwargs):
		requested = filters["name"][1]
		return [
			frappe._dict(name=sku, current_selling_price=selling_prices.get(sku))
			for sku in requested
			if sku in selling_prices
		]

	import frappe

	with (
		patch.object(frappe, "get_cached_value", side_effect=fake_get_cached_value),
		patch.object(frappe, "get_all", side_effect=fake_get_all),
	):
		yield


def make_record(proxy_sku, min_price, max_price):
	return {
		"name": f"APP-{proxy_sku}",
		"proxy_sku": proxy_sku,
		"current_min_price": min_price,
		"current_max_price": max_price,
	}


class TestPriceGuard(IntegrationTestCase):
	def test_drop_beyond_threshold_is_blocked(self):
		# current (10) above the new ceiling (7) → forced 30% drop
		record = make_record("SKU-1", min_price=5.0, max_price=7.0)
		with mock_settings(threshold=20, direction="Both", selling_prices={"SKU-1": 10.0}):
			allowed, blocked = price_guard.partition_pricing_by_price_deviation([record])

		self.assertEqual(allowed, [])
		self.assertEqual(len(blocked), 1)
		self.assertEqual(blocked[0]["proxy_sku"], "SKU-1")
		self.assertAlmostEqual(blocked[0]["deviation_pct"], 30.0)

	def test_rise_beyond_threshold_is_blocked(self):
		# current (10) below the new floor (13) → forced 30% rise
		record = make_record("SKU-2", min_price=13.0, max_price=15.0)
		with mock_settings(threshold=20, direction="Both", selling_prices={"SKU-2": 10.0}):
			allowed, blocked = price_guard.partition_pricing_by_price_deviation([record])

		self.assertEqual(allowed, [])
		self.assertEqual(len(blocked), 1)
		self.assertAlmostEqual(blocked[0]["deviation_pct"], 30.0)

	def test_current_inside_band_is_allowed(self):
		record = make_record("SKU-3", min_price=9.0, max_price=11.0)
		with mock_settings(threshold=20, direction="Both", selling_prices={"SKU-3": 10.0}):
			allowed, blocked = price_guard.partition_pricing_by_price_deviation([record])

		self.assertEqual(blocked, [])
		self.assertEqual(allowed, [record])

	def test_wide_band_with_current_at_ceiling_is_allowed(self):
		# wz-02 regression: 13.66-15.99 band is ~17% wide; current sits at the ceiling.
		# Band width must not masquerade as a 14.57% drop.
		record = make_record("WZ-02", min_price=13.66, max_price=15.99)
		with mock_settings(threshold=10, direction="Both", selling_prices={"WZ-02": 15.99}):
			allowed, blocked = price_guard.partition_pricing_by_price_deviation([record])

		self.assertEqual(blocked, [])
		self.assertEqual(allowed, [record])

	def test_missing_current_price_is_blocked(self):
		record = make_record("SKU-4", min_price=9.0, max_price=11.0)
		with mock_settings(threshold=20, direction="Both", selling_prices={}):
			allowed, blocked = price_guard.partition_pricing_by_price_deviation([record])

		self.assertEqual(allowed, [])
		self.assertEqual(len(blocked), 1)
		self.assertEqual(blocked[0]["reason"], "No current selling price")

	def test_zero_current_price_is_blocked(self):
		record = make_record("SKU-5", min_price=9.0, max_price=11.0)
		with mock_settings(threshold=20, direction="Both", selling_prices={"SKU-5": 0.0}):
			allowed, blocked = price_guard.partition_pricing_by_price_deviation([record])

		self.assertEqual(allowed, [])
		self.assertEqual(blocked[0]["reason"], "No current selling price")

	def test_drops_only_allows_big_rise(self):
		# current (10) below the new floor (13) forces a rise, but direction ignores rises
		record = make_record("SKU-6", min_price=13.0, max_price=20.0)
		with mock_settings(threshold=20, direction="Drops Only", selling_prices={"SKU-6": 10.0}):
			allowed, blocked = price_guard.partition_pricing_by_price_deviation([record])

		self.assertEqual(blocked, [])
		self.assertEqual(allowed, [record])

	def test_drops_only_blocks_big_drop(self):
		# current (10) above the new ceiling (7) → forced 30% drop
		record = make_record("SKU-7", min_price=5.0, max_price=7.0)
		with mock_settings(threshold=20, direction="Drops Only", selling_prices={"SKU-7": 10.0}):
			allowed, blocked = price_guard.partition_pricing_by_price_deviation([record])

		self.assertEqual(allowed, [])
		self.assertAlmostEqual(blocked[0]["deviation_pct"], 30.0)

	def test_increases_only_allows_big_drop(self):
		# current (10) above the new ceiling (7) forces a drop, but direction ignores drops
		record = make_record("SKU-8", min_price=2.0, max_price=7.0)
		with mock_settings(threshold=20, direction="Increases Only", selling_prices={"SKU-8": 10.0}):
			allowed, blocked = price_guard.partition_pricing_by_price_deviation([record])

		self.assertEqual(blocked, [])
		self.assertEqual(allowed, [record])

	def test_threshold_boundary_is_allowed(self):
		# current (10) exactly 20% above the ceiling (8) should not block (strict greater-than)
		record = make_record("SKU-9", min_price=5.0, max_price=8.0)
		with mock_settings(threshold=20, direction="Both", selling_prices={"SKU-9": 10.0}):
			allowed, blocked = price_guard.partition_pricing_by_price_deviation([record])

		self.assertEqual(blocked, [])
		self.assertEqual(allowed, [record])

	def test_mixed_batch_partitions_correctly(self):
		good = make_record("SKU-OK", min_price=9.5, max_price=10.5)
		crashed = make_record("SKU-CRASH", min_price=1.0, max_price=7.0)
		records = [good, crashed]
		prices = {"SKU-OK": 10.0, "SKU-CRASH": 10.0}
		with mock_settings(threshold=20, direction="Both", selling_prices=prices):
			allowed, blocked = price_guard.partition_pricing_by_price_deviation(records)

		self.assertEqual(allowed, [good])
		self.assertEqual(len(blocked), 1)
		self.assertEqual(blocked[0]["proxy_sku"], "SKU-CRASH")

	def test_empty_input_returns_empty(self):
		self.assertEqual(price_guard.partition_pricing_by_price_deviation([]), ([], []))
