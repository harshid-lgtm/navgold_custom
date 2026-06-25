# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.doctype.bulk_reset_purchase_cost_source.bulk_reset_purchase_cost_source import (
	EXPLICIT_TARGET_SOURCES,
	group_updates_by_target,
)


class TestBulkResetTargetSources(IntegrationTestCase):
	def test_item_tax_sync_is_a_valid_target(self):
		self.assertIn("Item-Tax-Sync", EXPLICIT_TARGET_SOURCES)

	def test_explicit_target_routes_all_rows_to_itself(self):
		rows = [
			{"cost_name": "C1", "cost_sub_type": "VAT-Standard"},
			{"cost_name": "C2", "cost_sub_type": "VAT-Standard"},
		]
		grouped = group_updates_by_target(rows, "Item-Tax-Sync")
		self.assertEqual(grouped, {"Item-Tax-Sync": ["C1", "C2"]})
