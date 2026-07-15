# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.price_calculator import VALUE_MODES
from navgold_custom.navgold_custom.doctype.bulk_reset_purchase_cost_source.bulk_reset_purchase_cost_source import (
	reset_purchase_cost_sources,
)


class TestBulkResetTargetModes(IntegrationTestCase):
	def test_contract_is_collapsed_to_auto_and_manual(self):
		self.assertEqual(VALUE_MODES, {"Auto", "Manual"})

	def test_old_feed_sources_are_no_longer_accepted_targets(self):
		# The 8-value source fan-out is dead; only the derived modes survive.
		for dropped in (
			"Supplier-Sync",
			"PO-Sync",
			"FBA-Transfer",
			"Item-Tax-Sync",
			"Shipping-Sync",
			"Auto-Sync",
		):
			self.assertNotIn(dropped, VALUE_MODES)

	def test_background_job_rejects_a_non_mode_target(self):
		with self.assertRaises(frappe.ValidationError):
			reset_purchase_cost_sources(
				filters={"cost_type": "Purchase Cost"},
				target_mode="Supplier-Sync",
				manual_value=None,
				enqueued_by="Administrator",
			)
