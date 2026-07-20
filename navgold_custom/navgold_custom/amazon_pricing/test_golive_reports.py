# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.data import flt

from navgold_custom.navgold_custom.report.amazon_go_live_data_readiness.amazon_go_live_data_readiness import (
	build_readiness_row,
)
from navgold_custom.navgold_custom.report.amazon_go_live_data_readiness.amazon_go_live_data_readiness import (
	execute as execute_readiness,
)
from navgold_custom.navgold_custom.report.amazon_order_sync_health.amazon_order_sync_health import (
	build_hourly_buckets,
)
from navgold_custom.navgold_custom.report.amazon_order_sync_health.amazon_order_sync_health import (
	execute as execute_sync_health,
)
from navgold_custom.navgold_custom.report.amazon_pricing_push_monitor.amazon_pricing_push_monitor import (
	execute as execute_push_monitor,
)
from navgold_custom.navgold_custom.report.below_min_price_sales.below_min_price_sales import (
	build_below_min_rows,
)
from navgold_custom.navgold_custom.report.below_min_price_sales.below_min_price_sales import (
	execute as execute_below_min,
)
from navgold_custom.navgold_custom.report.supplier_data_readiness.supplier_data_readiness import (
	execute as execute_supplier_readiness,
)


class TestGoliveReports(IntegrationTestCase):
	def test_all_reports_execute_on_default_filters(self):
		for execute in (
			execute_readiness,
			execute_supplier_readiness,
			execute_push_monitor,
			execute_sync_health,
			execute_below_min,
		):
			result = execute({})
			columns, data = result[0], result[1]
			self.assertIsInstance(columns, list)
			self.assertIsInstance(data, list)

	def test_below_min_rows_flag_deficit(self):
		so_rows = [
			frappe._dict(
				sales_order="SO-T1",
				creation="2026-06-12 10:00:00",
				item_code="ITEM-A",
				uom="Nos",
				qty=2,
				rate=5,
			),
			frappe._dict(
				sales_order="SO-T2",
				creation="2026-06-12 10:00:00",
				item_code="ITEM-B",
				uom="Nos",
				qty=1,
				rate=20,
			),
		]
		min_price_map = {
			("ITEM-A", "Nos"): {"min_price": 10.0, "proxy_sku": "PROXY-A"},
			("ITEM-B", "Nos"): {"min_price": 10.0, "proxy_sku": "PROXY-B"},
		}

		rows = build_below_min_rows(so_rows, min_price_map)

		below = next(row for row in rows if row["sales_order"] == "SO-T1")
		self.assertEqual(flt(below["deficit"]), 5.0)
		self.assertEqual(flt(below["estimated_loss"]), 10.0)
		above = next(row for row in rows if row["sales_order"] == "SO-T2")
		self.assertEqual(flt(above["deficit"]), -10.0)
		self.assertEqual(flt(above["estimated_loss"]), 0.0)

	def test_readiness_row_blocked_without_pricing(self):
		proxy = frappe._dict(name="PROXY-X", item="ITEM-X", is_fba=0, asin="B000TEST01")

		row = build_readiness_row(proxy, None, {}, {}, {})

		self.assertEqual(row["status"], "BLOCKED")
		self.assertIn("no Amazon Product Pricing", row["issues"])

	def test_readiness_row_ready_when_complete(self):
		proxy = frappe._dict(name="PROXY-Y", item="ITEM-Y", is_fba=1, asin="B000TEST02")
		pricing = frappe._dict(
			name="PROXY-Y",
			proxy_sku="PROXY-Y",
			is_active=1,
			active_strategy="PROXY-Y-FBA",
			current_min_price=10,
			current_max_price=20,
		)
		strategy_map = {
			"PROXY-Y-FBA": frappe._dict(name="PROXY-Y-FBA", is_active=1, min_margin=10, max_margin=20)
		}
		purchase_cost_map = {"PROXY-Y-FBA": 5.0}

		row = build_readiness_row(proxy, pricing, strategy_map, purchase_cost_map, {})

		self.assertEqual(row["status"], "READY")
		self.assertEqual(row["issues"], "")

	def test_hourly_buckets_group_and_sort(self):
		buckets = build_hourly_buckets(
			["2026-06-12 10:05:00", "2026-06-12 10:55:00", "2026-06-12 11:01:00"],
			["2026-06-12 10:30:00"],
		)

		self.assertEqual(buckets[0]["hour_bucket"], "2026-06-12 11:00")
		self.assertEqual(buckets[0]["sales_order_count"], 1)
		self.assertEqual(buckets[1]["hour_bucket"], "2026-06-12 10:00")
		self.assertEqual(buckets[1]["sales_order_count"], 2)
		self.assertEqual(buckets[1]["failed_sync_count"], 1)
