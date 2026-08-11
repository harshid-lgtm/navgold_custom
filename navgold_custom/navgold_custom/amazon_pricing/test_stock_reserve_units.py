# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.utils import (
	allocate_pack_stock,
	apply_stock_reserve,
)

PACK_POOL = {"DUPx2": 2.0, "DUPx3": 3.0, "DUPx6": 6.0}


def deal(available_qty, reserve_percentage, pool=None):
	return allocate_pack_stock(apply_stock_reserve(available_qty, reserve_percentage), pool or PACK_POOL)


def units_dealt(allocation, pool=None):
	pool = pool or PACK_POOL
	return sum(pool[proxy_sku] * packs for proxy_sku, packs in allocation.items())


class TestStockReserveOnUnits(IntegrationTestCase):
	def test_a_single_pack_survives_the_reserve(self):
		"""The reported bug: one pack times a reserve floors to zero, wiping the listing."""
		allocation = deal(17, 10)

		self.assertGreater(allocation["DUPx6"], 0)
		self.assertGreater(allocation["DUPx3"], 0)
		self.assertGreater(allocation["DUPx2"], 0)

	def test_the_reserve_holds_back_what_it_says(self):
		allocation = deal(100, 10)

		self.assertLessEqual(units_dealt(allocation), 90)
		self.assertGreaterEqual(units_dealt(allocation), 85)

	def test_a_quarter_reserve_still_leaves_every_listing_live(self):
		allocation = deal(17, 25)

		self.assertEqual(sum(1 for packs in allocation.values() if packs == 0), 0)
		self.assertLessEqual(units_dealt(allocation), 12)

	def test_no_reserve_deals_everything_it_can(self):
		allocation = deal(17, 0)

		self.assertEqual(units_dealt(allocation), 16)

	def test_a_full_reserve_publishes_nothing(self):
		allocation = deal(100, 100)

		self.assertEqual(units_dealt(allocation), 0)

	def test_a_negative_reserve_cannot_inflate_the_deal(self):
		self.assertEqual(apply_stock_reserve(100, -10), 100)
		self.assertEqual(units_dealt(deal(17, -10)), 16)

	def test_a_reserve_over_a_hundred_is_clamped(self):
		self.assertEqual(apply_stock_reserve(100, 150), 0)

	def test_the_reserve_never_deals_more_than_it_held_back(self):
		"""The oversell invariant has to survive the reserve at every size."""
		for available_qty in range(0, 120):
			for reserve_percentage in (0, 5, 10, 25, 50, 90):
				allocation = deal(available_qty, reserve_percentage)
				sellable = apply_stock_reserve(available_qty, reserve_percentage)
				self.assertLessEqual(
					units_dealt(allocation),
					sellable,
					f"dealt more than the reserve allowed at qty={available_qty} pct={reserve_percentage}",
				)

	def test_a_one_pack_item_is_not_wiped(self):
		"""An item whose only listing is a single pack must still publish it."""
		allocation = deal(6, 10, pool={"DUPx6": 6.0})

		self.assertEqual(allocation["DUPx6"], 0)
		self.assertEqual(deal(12, 10, pool={"DUPx6": 6.0})["DUPx6"], 1)
