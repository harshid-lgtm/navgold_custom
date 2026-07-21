# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.data import add_to_date, now_datetime

from navgold_custom.navgold_custom.amazon_pricing.control_center import (
	compute_outbound_scope,
	get_oldest_open_feed_minutes,
)

# Prod canary posture: selected-price allowlist, Amazon Stock Only, Repricer live.
CANARY_STOCK_ONLY = {
	"amazon_stock_live": True,
	"amazon_prices_live": False,
	"repricer_live": True,
}

READINESS_ROWS = [
	{"proxy_sku": "PROXY-CANARY-READY", "violations": ""},
	{"proxy_sku": "PROXY-CANARY-BLOCKED", "violations": "Purchase cost is 0"},
	{"proxy_sku": "PROXY-OUTSIDE-READY", "violations": ""},
]


class TestControlCenterScope(IntegrationTestCase):
	def test_stock_only_canary_gates_stock_and_prices(self):
		scope = compute_outbound_scope(
			CANARY_STOCK_ONLY, READINESS_ROWS, {"PROXY-CANARY-READY", "PROXY-CANARY-BLOCKED"}, None
		)

		# Stock Only mode => zero prices reach Amazon even though SKUs are "ready".
		self.assertEqual(scope["amazon_price"], 0)
		# Only the ready SKU inside the canary allowlist gets a price to Repricer — the blocked
		# canary SKU and the ready SKU outside the allowlist are both excluded.
		self.assertEqual(scope["repricer_price"], 1)
		# Stock is gated by the SAME canary allowlist (single-writer boundary): only the two canary
		# SKUs are stock-fed, NOT the non-canary ready SKU. The blocked canary SKU IS stock-fed
		# because stock ignores price validity.
		self.assertEqual(scope["amazon_stock"], 2)
		self.assertEqual(scope["amazon_stock_suppressed"], 0)
		self.assertEqual(scope["canary_total"], 2)
		self.assertEqual(scope["canary_mode"], "Selected")

	def test_zero_stock_suppression_reduces_real_stock_footprint(self):
		# One canary SKU is on the zero-stock list — it rides the feed but pushes 0, so it is not
		# publishing real stock.
		scope = compute_outbound_scope(
			CANARY_STOCK_ONLY,
			READINESS_ROWS,
			{"PROXY-CANARY-READY", "PROXY-CANARY-BLOCKED"},
			{"PROXY-CANARY-BLOCKED"},
		)

		# Two canary SKUs in the feed, one zeroed => one publishing real stock.
		self.assertEqual(scope["amazon_stock"], 1)
		self.assertEqual(scope["amazon_stock_suppressed"], 1)

	def test_publish_all_scopes_prices_to_every_ready_sku(self):
		# allowed=None models "Publish All Prices" — no canary restriction.
		scope = compute_outbound_scope(
			{"amazon_stock_live": True, "amazon_prices_live": True, "repricer_live": True},
			READINESS_ROWS,
			None,
			None,
		)

		# Both ready SKUs get prices; the blocked one does not.
		self.assertEqual(scope["amazon_price"], 2)
		self.assertEqual(scope["repricer_price"], 2)
		# No canary gate => stock covers every active SKU.
		self.assertEqual(scope["amazon_stock"], len(READINESS_ROWS))
		self.assertEqual(scope["canary_mode"], "All")

	def test_frozen_channels_zero_every_destination(self):
		scope = compute_outbound_scope(
			{"amazon_stock_live": False, "amazon_prices_live": False, "repricer_live": False},
			READINESS_ROWS,
			None,
			None,
		)

		self.assertEqual(scope["amazon_stock"], 0)
		self.assertEqual(scope["amazon_price"], 0)
		self.assertEqual(scope["repricer_price"], 0)

	def test_oldest_open_feed_ignores_pre_window_orphans(self):
		log = frappe.new_doc("Amazon Pricing Submission Log")
		log.submission_type = "Amazon"
		log.status = "Submitted"
		log.insert(ignore_permissions=True)
		# Backdate it to 40 days ago — an un-reconciled orphan, exactly what inflated the live metric.
		frappe.db.set_value(
			"Amazon Pricing Submission Log",
			log.name,
			"creation",
			add_to_date(now_datetime(), days=-40),
			update_modified=False,
		)

		# A 48h window must not see the 40-day-old feed.
		recent_cutoff = add_to_date(now_datetime(), hours=-48)
		self.assertEqual(get_oldest_open_feed_minutes(recent_cutoff), 0)

		# A window wide enough to include it does surface the stall.
		wide_cutoff = add_to_date(now_datetime(), days=-60)
		self.assertGreater(get_oldest_open_feed_minutes(wide_cutoff), 0)
