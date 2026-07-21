# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.data import add_to_date, now_datetime

from navgold_custom.navgold_custom.amazon_pricing.control_center import (
	compute_outbound_scope,
	get_oldest_open_feed_minutes,
)

# Canary posture live on prod: prices restricted to a selected allowlist, Amazon on Stock Only,
# Repricer live — the exact combination that made "4119 ready to push" look alarming.
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
	def test_stock_only_canary_sends_no_prices_to_amazon(self):
		scope = compute_outbound_scope(
			CANARY_STOCK_ONLY, READINESS_ROWS, {"PROXY-CANARY-READY", "PROXY-CANARY-BLOCKED"}
		)

		# Stock Only mode => zero prices reach Amazon even though SKUs are "ready".
		self.assertEqual(scope["amazon_price"], 0)
		# Only the ready SKU inside the canary allowlist gets a price to Repricer — the blocked
		# canary SKU and the ready SKU outside the allowlist are both excluded.
		self.assertEqual(scope["repricer_price"], 1)
		# Stock ignores price validity and the allowlist: every active SKU is stock-fed.
		self.assertEqual(scope["amazon_stock"], len(READINESS_ROWS))
		self.assertEqual(scope["canary_total"], 2)
		self.assertEqual(scope["canary_mode"], "Selected")

	def test_publish_all_scopes_prices_to_every_ready_sku(self):
		# allowed=None models "Publish All Prices" — no canary restriction.
		scope = compute_outbound_scope(
			{"amazon_stock_live": True, "amazon_prices_live": True, "repricer_live": True},
			READINESS_ROWS,
			None,
		)

		# Both ready SKUs get prices; the blocked one does not.
		self.assertEqual(scope["amazon_price"], 2)
		self.assertEqual(scope["repricer_price"], 2)
		self.assertEqual(scope["canary_mode"], "All")

	def test_frozen_channels_zero_every_destination(self):
		scope = compute_outbound_scope(
			{"amazon_stock_live": False, "amazon_prices_live": False, "repricer_live": False},
			READINESS_ROWS,
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
