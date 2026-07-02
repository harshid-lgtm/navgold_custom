# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

# Wired-flow teeth for the two review-fix behaviours settled after the first cost-tree flow run:
#   2a) bulk-reset-to-Auto must NOT zero an externally-owned (Amazon-Sync / FBA-fee) cost value —
#       populate_costs skips EXTERNALLY_OWNED_SOURCES, so the fee the fetcher wrote survives the
#       reset + the enqueued strategy recompute. We drive the real background job.
#   2b) the Purchase Cost Overview report bulk-update reads the RENAMED payload key `value_mode`
#       (was `source`); we prove the new key updates + re-prices and the old key is inert.
# Real chain, rolled back. The reset job commits per chunk (F7) so we neutralise frappe.db.commit,
# and it enqueues the recompute (F3) so we force enqueue to the foreground.

from functools import partial
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing import price_calculator
from navgold_custom.navgold_custom.amazon_pricing.fba_fee_fetcher import FEE_SOURCE
from navgold_custom.navgold_custom.amazon_pricing.test_cost_tree_flow import (
	FED_LEAF,
	FIXED_SETTINGS,
	PURCHASE_GROUP,
	create_proxy_sku,
	ensure_cost_tree,
	ensure_fed_leaf,
	ensure_item,
	persisted_costs,
)
from navgold_custom.navgold_custom.doctype.bulk_reset_purchase_cost_source.bulk_reset_purchase_cost_source import (
	reset_purchase_cost_sources,
)
from navgold_custom.navgold_custom.report.amazon_purchase_cost_overview.amazon_purchase_cost_overview import (
	bulk_update_purchase_cost_source,
)

FEE_LEAF = f"{PURCHASE_GROUP}-AmazonFee"
FEE_VALUE = 5.0


def ensure_fee_leaf():
	# A leaf whose source is Amazon-Sync (owned by the FBA fee fetcher, in EXTERNALLY_OWNED_SOURCES).
	ensure_fed_leaf(PURCHASE_GROUP, "AmazonFee", FEE_SOURCE)


def insert_with_fixed_settings(strategy):
	with patch.object(price_calculator, "get_pricing_settings", return_value=FIXED_SETTINGS):
		strategy.insert()
	return strategy


def build_fee_strategy(proxy_sku):
	# One rank-1 Amazon-Sync row carrying a fetcher-written value, pinned Manual to mimic the
	# stored fee. On insert populate_costs skips it (Manual), so the value persists as-is.
	strategy = frappe.new_doc("Amazon Product Strategy")
	strategy.proxy_sku = proxy_sku
	strategy.strategy_name = "default"
	strategy.is_active = 1
	strategy.min_margin = 10
	strategy.max_margin = 20
	strategy.append(
		"costs",
		{
			"cost_type": PURCHASE_GROUP,
			"cost_sub_type": FEE_LEAF,
			"value_mode": "Manual",
			"value": FEE_VALUE,
			"rank": 1,
		},
	)
	return strategy


def build_fed_purchase_strategy(proxy_sku):
	# One rank-1 fed purchase row (FBA-Transfer → last_transfer_rate) — the report's bulk-update target.
	strategy = frappe.new_doc("Amazon Product Strategy")
	strategy.proxy_sku = proxy_sku
	strategy.strategy_name = "default"
	strategy.is_active = 1
	strategy.min_margin = 10
	strategy.max_margin = 20
	strategy.append(
		"costs",
		{"cost_type": PURCHASE_GROUP, "cost_sub_type": FED_LEAF, "value_mode": "Auto", "rank": 1},
	)
	return strategy


def run_reset_to_auto(proxy_sku):
	# The real background job: neutralise its per-chunk commit (F7) and force the recompute
	# it enqueues onto the foreground (F3) so the whole chain runs inside the test transaction.
	filters = {"cost_type": PURCHASE_GROUP, "proxy_sku": proxy_sku}
	with (
		patch("frappe.db.commit", new=MagicMock()),
		patch("frappe.enqueue", new=partial(frappe.enqueue, now=True)),
	):
		reset_purchase_cost_sources(
			filters=filters, target_mode="Auto", manual_value=None, enqueued_by="Administrator"
		)


class TestCostTreeReviewFlow(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cost_tree()
		ensure_fee_leaf()
		ensure_item()
		price_calculator.get_cost_type_map.clear_cache()
		price_calculator.get_cost_sub_type_map.clear_cache()

	def test_bulk_reset_to_auto_preserves_externally_owned_fee_value(self):
		proxy_sku = create_proxy_sku(last_transfer_rate=4.0)
		strategy = insert_with_fixed_settings(build_fee_strategy(proxy_sku))

		# Before: the fetcher-owned value sits pinned Manual.
		(before,) = persisted_costs(strategy.name)
		self.assertEqual(
			(before.cost_sub_type, before.value, before.value_mode), (FEE_LEAF, FEE_VALUE, "Manual")
		)

		run_reset_to_auto(proxy_sku)

		# After the reset + enqueued recompute: mode flipped to Auto, value NOT zeroed (externally owned).
		(after,) = persisted_costs(strategy.name)
		self.assertEqual(after.value_mode, "Auto")
		self.assertEqual(after.value, FEE_VALUE)

	def test_report_bulk_update_uses_value_mode_key(self):
		proxy_sku = create_proxy_sku(last_transfer_rate=4.0)
		strategy = insert_with_fixed_settings(build_fed_purchase_strategy(proxy_sku))

		(before,) = persisted_costs(strategy.name)
		self.assertEqual((before.value, before.value_mode), (4.0, "Auto"))
		min_before = frappe.db.get_value("Amazon Product Strategy", strategy.name, "calculated_min_price")

		result = bulk_update_purchase_cost_source(
			frappe.as_json([{"strategy": strategy.name, "value_mode": "Manual", "value": 12.5}])
		)

		self.assertEqual(result["updated"], 1)
		self.assertEqual(result["skipped"], [])

		(after,) = persisted_costs(strategy.name)
		self.assertEqual((after.value, after.value_mode), (12.5, "Manual"))
		# The strategy re-priced: a bigger purchase cost lifts the computed floor.
		min_after = frappe.db.get_value("Amazon Product Strategy", strategy.name, "calculated_min_price")
		self.assertGreater(min_after, min_before)

	def test_report_bulk_update_rejects_old_source_key(self):
		proxy_sku = create_proxy_sku(last_transfer_rate=4.0)
		strategy = insert_with_fixed_settings(build_fed_purchase_strategy(proxy_sku))

		# The pre-rename payload key `source` is now inert: no value_mode → skipped, nothing persisted.
		result = bulk_update_purchase_cost_source(
			frappe.as_json([{"strategy": strategy.name, "source": "Manual", "value": 12.5}])
		)

		self.assertEqual(result["updated"], 0)
		self.assertEqual(len(result["skipped"]), 1)

		(unchanged,) = persisted_costs(strategy.name)
		self.assertEqual((unchanged.value, unchanged.value_mode), (4.0, "Auto"))
