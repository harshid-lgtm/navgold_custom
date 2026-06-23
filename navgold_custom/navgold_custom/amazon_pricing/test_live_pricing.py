# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing import live_pricing_fetcher
from navgold_custom.navgold_custom.amazon_pricing.live_pricing_fetcher import (
	fetch_live_prices,
	parse_purchasable_offer,
)

SNAPSHOT_MODULE = "navgold_custom.navgold_custom.doctype.amazon_pricing_snapshot.amazon_pricing_snapshot"


def offer(minimum, maximum, our_price):
	return {
		"purchasable_offer": [
			{
				"minimum_seller_allowed_price": [{"schedule": [{"value_with_tax": minimum}]}],
				"maximum_seller_allowed_price": [{"schedule": [{"value_with_tax": maximum}]}],
				"our_price": [{"schedule": [{"value_with_tax": our_price}]}],
			}
		]
	}


class TestLivePricingParse(IntegrationTestCase):
	def test_parse_extracts_band_and_selling_price(self):
		parsed = parse_purchasable_offer(offer(8.86, 14.99, 9.5))
		self.assertEqual(parsed, {"min": 8.86, "max": 14.99, "selling_price": 9.5})

	def test_parse_returns_none_without_offer(self):
		self.assertIsNone(parse_purchasable_offer({}))

	def test_parse_handles_partial_offer(self):
		# Max present, min absent — extraction must not blow up, just yield None for the gap.
		parsed = parse_purchasable_offer(
			{
				"purchasable_offer": [
					{"maximum_seller_allowed_price": [{"schedule": [{"value_with_tax": 10}]}]}
				]
			}
		)
		self.assertIsNone(parsed["min"])
		self.assertEqual(parsed["max"], 10.0)

	def test_fetch_live_prices_maps_by_sku_and_skips_offerless(self):
		items = [
			{"sku": "SKU-A", "attributes": offer(5.0, 10.0, 7.0)},
			{"sku": "SKU-NO-OFFER", "attributes": {}},
			{"attributes": offer(1.0, 2.0, 1.5)},  # no sku
		]
		client = MagicMock()
		client.search_items_by_skus.return_value = items
		with (
			patch.object(
				live_pricing_fetcher,
				"get_active_seller_skus",
				return_value=["SKU-A", "SKU-NO-OFFER"],
			),
			patch.object(live_pricing_fetcher.Listings, "from_amazon_settings", return_value=client),
		):
			prices = fetch_live_prices()

		self.assertEqual(set(prices), {"SKU-A"})
		self.assertEqual(prices["SKU-A"], {"min": 5.0, "max": 10.0, "selling_price": 7.0})
		client.search_items_by_skus.assert_called_once_with(
			["SKU-A", "SKU-NO-OFFER"], included_data="attributes"
		)

	def test_fetch_live_prices_empty_when_no_active_skus(self):
		with patch.object(live_pricing_fetcher, "get_active_seller_skus", return_value=[]):
			self.assertEqual(fetch_live_prices(), {})


class TestSnapshotPopulate(IntegrationTestCase):
	def pricing_rows(self):
		return [
			frappe._dict(
				name="SKU-A",
				proxy_sku="SKU-A",
				active_strategy="FBA",
				is_active=1,
				fixed_price=0,
				fixed_price_value=0,
			),
			frappe._dict(
				name="SKU-DIS",
				proxy_sku="SKU-DIS",
				active_strategy="SFP",
				is_active=0,
				fixed_price=1,
				fixed_price_value=5.0,
			),
		]

	def populate(self, live_prices, fetch_raises=False):
		doc = frappe.new_doc("Amazon Pricing Snapshot")
		fetch_kwargs = {"side_effect": Exception("boom")} if fetch_raises else {"return_value": live_prices}
		with (
			patch.object(frappe, "get_all", return_value=self.pricing_rows()),
			patch.object(frappe, "log_error"),
			patch(f"{SNAPSHOT_MODULE}.fetch_live_prices", **fetch_kwargs),
		):
			doc.populate_from_amazon_listings()
		return doc

	def test_captures_every_live_listing_with_system_status(self):
		# Amazon is the source of truth: active, disabled and unmanaged SKUs all land in the snapshot.
		doc = self.populate(
			{
				"SKU-A": {"min": 5.0, "max": 10.0, "selling_price": 7.0},
				"SKU-DIS": {"min": 3.0, "max": 6.0, "selling_price": 4.0},
				"SKU-UNKNOWN": {"min": 1.0, "max": 2.0, "selling_price": 1.5},
			}
		)
		by_sku = {row["proxy_sku"]: row for row in doc.get_rows()}

		self.assertEqual(set(by_sku), {"SKU-A", "SKU-DIS", "SKU-UNKNOWN"})
		self.assertEqual(by_sku["SKU-A"]["system_status"], "Active")
		self.assertEqual(by_sku["SKU-DIS"]["system_status"], "Disabled")
		self.assertEqual(by_sku["SKU-UNKNOWN"]["system_status"], "Not in System")
		self.assertEqual(doc.item_count, 3)
		self.assertEqual(doc.price_source, "Amazon Live")

	def test_band_always_from_amazon_with_pricing_metadata(self):
		doc = self.populate({"SKU-DIS": {"min": 3.0, "max": 6.0, "selling_price": 4.0}})
		row = doc.get_rows()[0]
		# Band is Amazon's; fixed-price metadata still comes from our pricing record.
		self.assertEqual(row["min_price"], 3.0)
		self.assertEqual(row["max_price"], 6.0)
		self.assertEqual(row["source"], "Amazon")
		self.assertEqual(row["pricing"], "SKU-DIS")
		self.assertEqual(row["fixed_price"], 1)
		self.assertEqual(row["fixed_price_value"], 5.0)

	def test_unmanaged_sku_has_no_pricing_link(self):
		# Not in System: carries Amazon's band but no pricing record to publish through.
		doc = self.populate({"SKU-UNKNOWN": {"min": 1.0, "max": 2.0, "selling_price": 1.5}})
		row = doc.get_rows()[0]
		self.assertIsNone(row["pricing"])
		self.assertEqual(row["proxy_sku"], "SKU-UNKNOWN")
		self.assertEqual(row["min_price"], 1.0)
		self.assertEqual(row["fixed_price"], 0)

	def test_partial_live_band_is_skipped(self):
		# Min present but no max — not a usable band, so the listing is dropped.
		doc = self.populate({"SKU-A": {"min": 5.0, "max": None, "selling_price": 7.0}})
		self.assertEqual(doc.get_rows(), [])
		self.assertEqual(doc.amazon_sourced_count, 0)

	def test_api_failure_raises_and_captures_nothing(self):
		# Amazon is the only trusted source — a total fetch failure must abort, not fall back.
		with self.assertRaises(Exception):
			self.populate({}, fetch_raises=True)
