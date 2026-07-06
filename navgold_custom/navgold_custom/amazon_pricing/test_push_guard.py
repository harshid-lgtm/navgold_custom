# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.tests.utils import change_settings

from navgold_custom.navgold_custom.amazon_pricing import amazon_feed_submitter
from navgold_custom.navgold_custom.amazon_pricing.cost_tree_test_fixtures import (
	build_strategy,
	create_proxy_sku,
	ensure_cost_group,
	ensure_cost_leaf,
	ensure_item,
)
from navgold_custom.navgold_custom.amazon_pricing.pricing_validators import (
	collect_pricing_violations,
	get_push_block_violations,
)

ITEM_CODE = "TEST-PUSH-GUARD-ITEM"
PURCHASE_COST_TYPE = "Test Push Guard Purchase"
PERCENT_COST_TYPE = "Test Push Guard Percent"
FBA_SHIPPING_COST_TYPE = "Test Push Guard FBA Shipping"


def build_guard_strategy(rows):
	return build_strategy(create_proxy_sku(item=ITEM_CODE, uom="Nos"), rows)


def healthy_purchase_row(value=5.0):
	return {
		"cost_type": PURCHASE_COST_TYPE,
		"cost_sub_type": ensure_cost_leaf(PURCHASE_COST_TYPE, "Top Supplier", source="Supplier-Sync"),
		"value": value,
		"value_mode": "Manual",
		"rank": 1,
		"is_active": 1,
	}


class TestPushGuardHelper(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_item(ITEM_CODE)
		ensure_cost_group(PURCHASE_COST_TYPE, "Absolute", is_purchase_cost=1)
		ensure_cost_group(PERCENT_COST_TYPE, "Relative")
		ensure_cost_group(f"{PERCENT_COST_TYPE} VAT", "VAT")
		ensure_cost_group(FBA_SHIPPING_COST_TYPE, "Absolute")

	def test_zero_relative_percentage_blocks(self):
		strategy = build_guard_strategy(
			[
				healthy_purchase_row(),
				{
					"cost_type": PERCENT_COST_TYPE,
					"value": 0,
					"value_mode": "Manual",
					"rank": 1,
					"is_active": 1,
				},
			]
		)

		violations = get_push_block_violations([strategy.name])

		self.assertTrue(
			any("zero percentage" in reason for reason in violations[strategy.name]),
			violations[strategy.name],
		)

	def test_zero_vat_percentage_blocks(self):
		strategy = build_guard_strategy(
			[
				healthy_purchase_row(),
				{
					"cost_type": f"{PERCENT_COST_TYPE} VAT",
					"value": 0,
					"value_mode": "Manual",
					"rank": 1,
					"is_active": 1,
				},
			]
		)

		violations = get_push_block_violations([strategy.name])

		self.assertTrue(
			any("zero percentage" in reason for reason in violations[strategy.name]),
			violations[strategy.name],
		)

	def test_fed_auto_purchase_cost_at_zero_blocks(self):
		strategy = build_guard_strategy(
			[
				{
					"cost_type": PURCHASE_COST_TYPE,
					"cost_sub_type": ensure_cost_leaf(
						PURCHASE_COST_TYPE, "Auto Supplier", source="Supplier-Sync"
					),
					"value": 0,
					"value_mode": "Auto",
					"rank": 1,
					"is_active": 1,
				}
			]
		)

		violations = get_push_block_violations([strategy.name])

		self.assertTrue(
			any("unsynced purchase" in reason for reason in violations[strategy.name]),
			violations[strategy.name],
		)

	def test_absolute_non_purchase_zero_is_allowed(self):
		# FBA shipping legitimately resolves to 0 on FBA strategies — must never block.
		strategy = build_guard_strategy(
			[
				healthy_purchase_row(),
				{
					"cost_type": FBA_SHIPPING_COST_TYPE,
					"value": 0,
					"value_mode": "Manual",
					"rank": 1,
					"is_active": 1,
				},
			]
		)

		violations = get_push_block_violations([strategy.name])

		self.assertNotIn(strategy.name, violations)

	def test_manual_purchase_cost_at_zero_is_allowed(self):
		# Rule (b) only catches Auto-fed purchase costs; a Manual 0 is the seller's call.
		strategy = build_guard_strategy(
			[
				{
					"cost_type": PURCHASE_COST_TYPE,
					"cost_sub_type": ensure_cost_leaf(
						PURCHASE_COST_TYPE, "Manual Supplier", source="Supplier-Sync"
					),
					"value": 0,
					"value_mode": "Manual",
					"rank": 1,
					"is_active": 1,
				}
			]
		)

		violations = get_push_block_violations([strategy.name])

		self.assertNotIn(strategy.name, violations)

	def test_manual_percentage_at_zero_still_blocks(self):
		# Rule (a) is unconditional on value_mode: a hand-pinned 0% is still wrong.
		strategy = build_guard_strategy(
			[
				healthy_purchase_row(),
				{
					"cost_type": PERCENT_COST_TYPE,
					"value": 0,
					"value_mode": "Manual",
					"rank": 1,
					"is_active": 1,
				},
			]
		)

		violations = get_push_block_violations([strategy.name])

		self.assertIn(strategy.name, violations)

	def test_healthy_strategy_is_pushable(self):
		strategy = build_guard_strategy(
			[
				healthy_purchase_row(value=5),
				{
					"cost_type": PERCENT_COST_TYPE,
					"value": 15,
					"value_mode": "Manual",
					"rank": 1,
					"is_active": 1,
				},
			]
		)

		violations = get_push_block_violations([strategy.name])

		self.assertNotIn(strategy.name, violations)

	def test_inactive_cost_row_does_not_block(self):
		# rank>1 rows are deactivated by the strategy's populate_costs, so a 0% sitting on an
		# inactive (superseded) row must never block the push.
		strategy = build_guard_strategy(
			[
				healthy_purchase_row(),
				{"cost_type": PERCENT_COST_TYPE, "value": 0, "value_mode": "Manual", "rank": 2},
			]
		)
		self.assertEqual(
			frappe.db.get_value(
				"Amazon Product Cost", {"parent": strategy.name, "cost_type": PERCENT_COST_TYPE}, "is_active"
			),
			0,
		)

		violations = get_push_block_violations([strategy.name])

		self.assertNotIn(strategy.name, violations)


class TestPushGuardBulkPush(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_item(ITEM_CODE)
		ensure_cost_group(PURCHASE_COST_TYPE, "Absolute", is_purchase_cost=1)
		ensure_cost_group(PERCENT_COST_TYPE, "Relative")

	def create_pricing(self, strategy, selling_price=20):
		frappe.db.set_value("Proxy SKU", strategy.proxy_sku, "current_selling_price", selling_price)
		pricing = frappe.new_doc("Amazon Product Pricing")
		pricing.proxy_sku = strategy.proxy_sku
		pricing.is_active = 1
		pricing.default_strategy = strategy.name
		pricing.insert()
		# Give it sane prices so only the cost violation is in play.
		frappe.db.set_value(
			"Amazon Product Pricing",
			pricing.name,
			{"current_min_price": 12, "current_max_price": 20},
			update_modified=False,
		)
		return pricing.name

	def test_bulk_push_skips_violator_pushes_healthy(self):
		healthy_strategy = build_guard_strategy(
			[
				healthy_purchase_row(value=5),
				{
					"cost_type": PERCENT_COST_TYPE,
					"value": 15,
					"value_mode": "Manual",
					"rank": 1,
					"is_active": 1,
				},
			]
		)
		violator_strategy = build_guard_strategy(
			[
				healthy_purchase_row(value=5),
				{
					"cost_type": PERCENT_COST_TYPE,
					"value": 0,
					"value_mode": "Manual",
					"rank": 1,
					"is_active": 1,
				},
			]
		)
		healthy_pricing = self.create_pricing(healthy_strategy)
		violator_pricing = self.create_pricing(violator_strategy)

		# Confirm the violation surfaces through the shared validator chokepoint.
		violations = collect_pricing_violations([violator_pricing], include_deviation=False)
		self.assertIn(violator_pricing, violations)
		self.assertNotIn(
			healthy_pricing, collect_pricing_violations([healthy_pricing], include_deviation=False)
		)

		captured = {}

		# Mock the Amazon transport boundary: capture the feed SKUs, never call out.
		class FakeFeeds:
			def __init__(self, *args, **kwargs):
				pass

			def create_feed_document(self):
				return {"feedDocumentId": "DOC", "url": "https://example.invalid/upload"}

			def upload_feed_content(self, url, content):
				feed = frappe.parse_json(content)
				captured["skus"] = [message["sku"] for message in feed["messages"]]

			def create_feed(self, feed_type, feed_document_id):
				return {"feedId": "FEED-1"}

		# Amazon SP API Settings is empty on a bare CI site; pin seller/marketplace so the feed
		# header validates. set_single_value (not change_settings) because this doctype's save()
		# override rejects ignore_permissions; the test transaction rolls it back.
		frappe.db.set_single_value("Amazon SP API Settings", {"merchant_id": "TEST-SELLER", "country": "GB"})
		with change_settings(
			"Navgold Settings",
			{
				"amazon_publishing_mode": "Stock and Prices",
				"pricing_publishing_mode": "Publish All Prices",
				"price_change_threshold_percent": 90,
				"price_change_block_direction": "Both",
				"send_min_price_to_amazon": 0,
			},
		):
			with patch.object(amazon_feed_submitter, "get_feeds_client", return_value=FakeFeeds()):
				result = amazon_feed_submitter.bulk_submit_pricing(
					frappe.as_json([healthy_pricing, violator_pricing])
				)

		self.assertTrue(result.get("success"), result)

		healthy_proxy = frappe.db.get_value("Amazon Product Pricing", healthy_pricing, "proxy_sku")
		violator_proxy = frappe.db.get_value("Amazon Product Pricing", violator_pricing, "proxy_sku")

		self.assertIn(healthy_proxy, captured.get("skus", []))
		self.assertNotIn(violator_proxy, captured.get("skus", []))

		blocked_proxies = {block["proxy_sku"] for block in result.get("blocked_validation", [])}
		self.assertIn(violator_proxy, blocked_proxies)
		self.assertNotIn(healthy_proxy, blocked_proxies)
