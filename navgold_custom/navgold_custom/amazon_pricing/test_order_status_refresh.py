# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from navgold_custom.navgold_custom import amazon_repository
from navgold_custom.navgold_custom.amazon_repository import (
	STATUS_REFRESH_MAX_WINDOW_HOURS,
	AmazonRepository,
)

ORDER_ID = "TEST-STATUS-REFRESH-001"


class TestOrderStatusRefresh(IntegrationTestCase):
	def setUp(self):
		self.settings = frappe.get_single("Amazon SP API Settings")
		if not (self.settings.company and self.settings.warehouse):
			self.skipTest("Amazon SP API Settings has no company/warehouse configured")

		self.clean_up()
		self.item_code = self.ensure_item()
		self.customer = self.ensure_customer("Test Status Refresh Buyer")

	def tearDown(self):
		self.clean_up()

	def clean_up(self):
		for name in frappe.get_all("Sales Order", filters={"amazon_order_id": ORDER_ID}, pluck="name"):
			# The cancel test leaves the order Closed, which blocks cancel(); reopen it first.
			frappe.db.set_value("Sales Order", name, "status", "To Deliver and Bill")
			sales_order = frappe.get_doc("Sales Order", name)
			if sales_order.docstatus == 1:
				sales_order.cancel()
			sales_order.delete(force=True)
		frappe.db.delete("Amazon Failed Sync Record", {"amazon_order_id": ORDER_ID})

	def ensure_item(self):
		item_code = "TEST-STATUS-REFRESH-ITEM"
		if not frappe.db.exists("Item", item_code):
			item = frappe.new_doc("Item")
			item.item_code = item_code
			item.item_name = item_code
			item.item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
			item.stock_uom = "Nos"
			item.is_stock_item = 0
			item.insert()
		return item_code

	def ensure_customer(self, customer_name):
		if not frappe.db.exists("Customer", customer_name):
			customer = frappe.new_doc("Customer")
			customer.customer_name = customer_name
			customer.customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
			customer.territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
			customer.insert()
		return customer_name

	def build_repository(self):
		repository = AmazonRepository.__new__(AmazonRepository)
		repository.amz_setting = self.settings
		repository.refresh_truncated = False
		return repository

	def build_order(self, order_status, amount):
		return {
			"AmazonOrderId": ORDER_ID,
			"OrderStatus": order_status,
			"FulfillmentChannel": "MFN",
			"MarketplaceId": "A1F83G8C2ARO7P",
			"PurchaseDate": "2026-07-20T10:00:00Z",
			"OrderTotal": {"Amount": amount},
		}

	def sync(self, repository, order, rate):
		items = [
			{
				"item_code": self.item_code,
				"item_name": self.item_code,
				"rate": rate,
				"qty": 1,
				"uom": "Nos",
				"stock_uom": "Nos",
				"allow_zero_valuation_rate": 1,
				"zero_qty_flag": False,
				"actual_qty": 0,
			}
		]
		with (
			patch.object(repository, "get_order_items", return_value=items),
			patch.object(repository, "create_customer", return_value=self.customer),
			patch.object(repository, "create_address", return_value=None),
		):
			return repository.create_sales_order(order)

	def refresh(self, repository, order, rate):
		items = [
			{
				"item_code": self.item_code,
				"item_name": self.item_code,
				"rate": rate,
				"qty": 1,
				"uom": "Nos",
				"stock_uom": "Nos",
				"allow_zero_valuation_rate": 1,
				"zero_qty_flag": False,
				"actual_qty": 0,
			}
		]
		with (
			patch.object(repository, "get_order_items", return_value=items),
			patch.object(repository, "create_customer", return_value=self.customer),
			patch.object(repository, "create_address", return_value=None),
		):
			return repository.update_order_status(order)

	def test_pending_order_is_parked_in_draft(self):
		repository = self.build_repository()

		# Amazon withholds ItemPrice while payment is unauthorised, so this arrives at rate 0.
		self.sync(repository, self.build_order("Pending", 0), rate=0)

		sales_order = frappe.get_value(
			"Sales Order",
			{"amazon_order_id": ORDER_ID},
			["name", "docstatus", "amazon_order_status", "grand_total"],
			as_dict=True,
		)
		self.assertEqual(sales_order.docstatus, 0, "a £0 Pending order must never be submitted")
		self.assertEqual(sales_order.amazon_order_status, "Pending")

	def test_pending_promotes_and_prices_when_payment_clears(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Pending", 0), rate=0)

		self.refresh(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		sales_order = frappe.get_value(
			"Sales Order",
			{"amazon_order_id": ORDER_ID},
			["name", "docstatus", "amazon_order_status", "grand_total"],
			as_dict=True,
		)
		self.assertEqual(sales_order.docstatus, 1, "a priced order must submit")
		self.assertEqual(sales_order.amazon_order_status, "Unshipped")
		self.assertGreater(sales_order.grand_total, 0, "promotion must pick up the real price")

	def test_submitted_status_change_leaves_money_untouched(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		before = frappe.get_value(
			"Sales Order", {"amazon_order_id": ORDER_ID}, ["name", "grand_total"], as_dict=True
		)
		self.refresh(repository, self.build_order("Shipped", 27.46), rate=99.99)

		after = frappe.get_value(
			"Sales Order",
			{"amazon_order_id": ORDER_ID},
			["docstatus", "amazon_order_status", "grand_total"],
			as_dict=True,
		)
		self.assertEqual(after.amazon_order_status, "Shipped")
		self.assertEqual(after.grand_total, before.grand_total, "a submitted order's total must never move")

	def test_cancel_closes_the_order_and_alerts(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		with patch.object(amazon_repository, "alert_order_canceled") as mock_alert:
			self.refresh(repository, self.build_order("Canceled", 27.46), rate=27.46)

		sales_order = frappe.get_value(
			"Sales Order",
			{"amazon_order_id": ORDER_ID},
			["amazon_order_status", "status"],
			as_dict=True,
		)
		self.assertEqual(sales_order.amazon_order_status, "Canceled")
		self.assertEqual(sales_order.status, "Closed")
		mock_alert.assert_called_once()

	def test_every_status_amazon_can_return_is_a_valid_option(self):
		# The refresh sends no OrderStatuses filter, so Amazon can hand back any status in the
		# lifecycle. db_set bypasses validation, so an unlisted one would be stored silently.
		options = frappe.get_meta("Sales Order").get_field("amazon_order_status").options.split("\n")
		amazon_statuses = [
			"PendingAvailability",
			"Pending",
			"Unshipped",
			"PartiallyShipped",
			"Shipped",
			"InvoiceUnconfirmed",
			"Canceled",
			"Unfulfillable",
		]

		self.assertEqual([status for status in amazon_statuses if status not in options], [])

	def test_partially_shipped_is_recorded_on_a_submitted_order(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		self.refresh(repository, self.build_order("PartiallyShipped", 27.46), rate=27.46)

		self.assertEqual(
			frappe.db.get_value("Sales Order", {"amazon_order_id": ORDER_ID}, "amazon_order_status"),
			"PartiallyShipped",
		)

	def test_pending_availability_is_parked_in_draft_like_pending(self):
		repository = self.build_repository()

		self.sync(repository, self.build_order("PendingAvailability", 0), rate=0)

		self.assertEqual(
			frappe.db.get_value("Sales Order", {"amazon_order_id": ORDER_ID}, "docstatus"),
			0,
			"a pre-order has no authorised payment either, so it must not submit",
		)

	def test_unchanged_status_is_a_noop(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		self.assertIsNone(self.refresh(repository, self.build_order("Unshipped", 27.46), rate=27.46))

	def test_unknown_order_is_left_to_the_create_path(self):
		repository = self.build_repository()
		order = self.build_order("Shipped", 27.46)
		order["AmazonOrderId"] = "TEST-STATUS-REFRESH-NEVER-SYNCED"

		self.assertIsNone(repository.update_order_status(order))

	def test_window_is_clamped_when_the_watermark_stalls(self):
		# A watermark frozen 30 days back must not produce a 30-day fetch window.
		stalled = add_to_date(now_datetime(), days=-30)
		self.settings.db_set("last_status_synced_at", stalled)

		with patch.object(AmazonRepository, "refresh_order_statuses", return_value=[]) as mock_refresh:
			amazon_repository.refresh_order_statuses()

		window_start = mock_refresh.call_args[0][0]
		floor = add_to_date(now_datetime(), hours=-STATUS_REFRESH_MAX_WINDOW_HOURS)
		self.assertGreaterEqual(get_datetime(window_start), add_to_date(floor, minutes=-1))

	def test_truncated_pass_does_not_advance_the_watermark(self):
		watermark = add_to_date(now_datetime(), hours=-2)
		self.settings.db_set("last_status_synced_at", watermark)

		def truncated_refresh(repository_self, window_start):
			repository_self.refresh_truncated = True
			return []

		with patch.object(AmazonRepository, "refresh_order_statuses", autospec=True) as mock_refresh:
			mock_refresh.side_effect = truncated_refresh
			amazon_repository.refresh_order_statuses()

		self.assertEqual(
			get_datetime(frappe.db.get_single_value("Amazon SP API Settings", "last_status_synced_at")),
			get_datetime(watermark),
			"a capped pass must resume from the same point, not skip the remainder",
		)
