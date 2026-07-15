# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.cost_tree_test_fixtures import (
	build_strategy,
	create_proxy_sku,
	ensure_cost_group,
	ensure_cost_leaf,
	ensure_item,
)
from navgold_custom.navgold_custom.report.amazon_go_live_data_readiness.amazon_go_live_data_readiness import (
	get_purchase_cost_map,
)
from navgold_custom.navgold_custom.report.amazon_purchase_cost_overview.amazon_purchase_cost_overview import (
	fetch_active_purchase_cost_rows,
)

PURCHASE_GROUP = "Test Report Purchase Cost"
PURCHASE_LEAF = f"{PURCHASE_GROUP}-Landed"
ITEM_CODE = "TEST-REPORT-COST-ITEM"


class TestPurchaseCostReportsTree(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cost_group(PURCHASE_GROUP, is_purchase_cost=1)
		ensure_cost_leaf(PURCHASE_GROUP, "Landed")
		ensure_item(ITEM_CODE)
		cls.strategy = build_strategy(
			create_proxy_sku(item=ITEM_CODE, uom="Nos"),
			[
				{
					"cost_type": PURCHASE_GROUP,
					"cost_sub_type": PURCHASE_LEAF,
					"value": 5.0,
					"value_mode": "Manual",
					"rank": 1,
				}
			],
			strategy_name="Default",
		)

	def test_readiness_purchase_cost_map_reads_group(self):
		cost_map = get_purchase_cost_map([self.strategy.name])
		self.assertEqual(cost_map.get(self.strategy.name), 5.0)

	def test_overview_returns_active_purchase_cost_row(self):
		rows = fetch_active_purchase_cost_rows({"proxy_sku": self.strategy.proxy_sku}, [PURCHASE_GROUP])
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["cost_sub_type"], PURCHASE_LEAF)
		self.assertEqual(rows[0]["current_value"], 5.0)
