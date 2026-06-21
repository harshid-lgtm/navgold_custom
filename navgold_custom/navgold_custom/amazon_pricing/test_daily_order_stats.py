# Copyright (c) 2026, Navgold and contributors
# For license information, please see license.txt

from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from navgold_custom.navgold_custom.amazon_pricing.daily_order_stats import (
	aggregate_daily_stats,
	recent_window,
)

HEADER = "amazon-order-id\tpurchase-date\torder-status\titem-price\tcurrency"


def make_report(rows):
	lines = [HEADER]
	for row in rows:
		lines.append(
			"\t".join(
				[
					row.get("order_id", ""),
					row.get("purchase_date", ""),
					row.get("status", ""),
					str(row.get("item_price", "")),
					row.get("currency", "GBP"),
				]
			)
		)
	return "\n".join(lines)


class TestRecentWindow(IntegrationTestCase):
	def test_window_is_n_days_ending_yesterday_inclusive(self):
		start_date, end_date = recent_window(4)
		# end_date is exclusive (today); start is 4 days before that, so the window covers
		# yesterday and the 3 days before it — 4 calendar days ending yesterday inclusive.
		self.assertEqual(end_date, getdate(add_days(today(), 1)))
		self.assertEqual(start_date, getdate(add_days(end_date, -4)))
		self.assertEqual((end_date - start_date).days, 4)

	def test_window_one_day_is_just_yesterday(self):
		start_date, end_date = recent_window(1)
		self.assertEqual((end_date - start_date).days, 1)
		self.assertEqual(start_date, getdate(add_days(today(), 0)))


class TestAggregateDailyStats(IntegrationTestCase):
	def test_multi_line_order_counted_once_gross_sums_lines(self):
		report = make_report(
			[
				{
					"order_id": "111",
					"purchase_date": "2026-06-01T09:00:00+00:00",
					"status": "Shipped",
					"item_price": 10,
				},
				{
					"order_id": "111",
					"purchase_date": "2026-06-01T09:00:00+00:00",
					"status": "Shipped",
					"item_price": 5,
				},
			]
		)
		stats = aggregate_daily_stats(report)
		day = stats["2026-06-01"]
		self.assertEqual(day["total_orders"], 1)
		self.assertEqual(day["shipped_orders"], 1)
		self.assertEqual(day["gross_amount"], 15.0)

	def test_cancelled_excluded_from_gross_but_counted(self):
		report = make_report(
			[
				{
					"order_id": "200",
					"purchase_date": "2026-06-02T09:00:00+00:00",
					"status": "Shipped",
					"item_price": 20,
				},
				{
					"order_id": "201",
					"purchase_date": "2026-06-02T11:00:00+00:00",
					"status": "Cancelled",
					"item_price": 99,
				},
			]
		)
		stats = aggregate_daily_stats(report)
		day = stats["2026-06-02"]
		self.assertEqual(day["total_orders"], 2)
		self.assertEqual(day["cancelled_orders"], 1)
		# 99 from the cancelled order must NOT appear in gross
		self.assertEqual(day["gross_amount"], 20.0)

	def test_status_prefix_matching(self):
		report = make_report(
			[
				{
					"order_id": "300",
					"purchase_date": "2026-06-03T09:00:00+00:00",
					"status": "Shipped - Delivered",
					"item_price": 7,
				},
				{
					"order_id": "301",
					"purchase_date": "2026-06-03T09:00:00+00:00",
					"status": "Cancelled by Seller",
					"item_price": 7,
				},
			]
		)
		stats = aggregate_daily_stats(report)
		day = stats["2026-06-03"]
		self.assertEqual(day["shipped_orders"], 1)
		self.assertEqual(day["cancelled_orders"], 1)
		self.assertEqual(day["gross_amount"], 7.0)

	def test_day_bucketing_splits_by_purchase_date(self):
		report = make_report(
			[
				{
					"order_id": "400",
					"purchase_date": "2026-06-04T23:00:00+00:00",
					"status": "Shipped",
					"item_price": 4,
				},
				{
					"order_id": "401",
					"purchase_date": "2026-06-05T01:00:00+00:00",
					"status": "Shipped",
					"item_price": 8,
				},
			]
		)
		stats = aggregate_daily_stats(report)
		self.assertEqual(set(stats), {"2026-06-04", "2026-06-05"})
		self.assertEqual(stats["2026-06-04"]["gross_amount"], 4.0)
		self.assertEqual(stats["2026-06-05"]["gross_amount"], 8.0)

	def test_rows_without_order_id_or_date_skipped(self):
		report = make_report(
			[
				{
					"order_id": "",
					"purchase_date": "2026-06-06T09:00:00+00:00",
					"status": "Shipped",
					"item_price": 5,
				},
				{"order_id": "500", "purchase_date": "", "status": "Shipped", "item_price": 5},
			]
		)
		stats = aggregate_daily_stats(report)
		self.assertEqual(stats, {})
