# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from navgold_custom.navgold_custom import amazon_repository
from navgold_custom.navgold_custom.amazon_repository import (
	AMAZON_ORDER_STATUSES,
	STATUS_REFRESH_MAX_PAGES,
	STATUS_REFRESH_MAX_WINDOW_HOURS,
	STATUS_REFRESH_WINDOW_HOURS,
	AmazonRepository,
	get_status_refresh_window,
)
from navgold_custom.navgold_custom.utils import get_draft_amazon_order_qty_map

ORDER_ID = "TEST-STATUS-REFRESH-001"


def build_items(item_code, rate):
	return [
		{
			"item_code": item_code,
			"item_name": item_code,
			"rate": rate,
			"qty": 1,
			"uom": "Nos",
			"stock_uom": "Nos",
			"allow_zero_valuation_rate": 1,
			"zero_qty_flag": False,
			"actual_qty": 0,
		}
	]


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
		with (
			patch.object(repository, "get_order_items", return_value=build_items(self.item_code, rate)),
			patch.object(repository, "create_customer", return_value=self.customer),
			patch.object(repository, "create_address", return_value=None),
		):
			return repository.create_sales_order(order)

	def refresh(self, repository, order, rate):
		existing = repository.get_sales_orders_by_amazon_id([ORDER_ID]).get(ORDER_ID)
		with (
			patch.object(repository, "get_order_items", return_value=build_items(self.item_code, rate)),
			patch.object(repository, "create_customer", return_value=self.customer),
			patch.object(repository, "create_address", return_value=None),
		):
			return repository.update_order_status(order, existing)

	def get_sales_order(self, *fields):
		return frappe.get_value("Sales Order", {"amazon_order_id": ORDER_ID}, list(fields), as_dict=True)

	def test_pending_order_is_parked_in_draft(self):
		repository = self.build_repository()

		# Amazon withholds ItemPrice while payment is unauthorised, so this arrives at rate 0.
		self.sync(repository, self.build_order("Pending", 0), rate=0)

		sales_order = self.get_sales_order("docstatus", "amazon_order_status")
		self.assertEqual(sales_order.docstatus, 0, "a £0 Pending order must never be submitted")
		self.assertEqual(sales_order.amazon_order_status, "Pending")

	def test_pending_promotes_and_prices_when_payment_clears(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Pending", 0), rate=0)

		self.refresh(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		sales_order = self.get_sales_order("docstatus", "amazon_order_status", "grand_total")
		self.assertEqual(sales_order.docstatus, 1, "a priced order must submit")
		self.assertEqual(sales_order.amazon_order_status, "Unshipped")
		self.assertGreater(sales_order.grand_total, 0, "promotion must pick up the real price")

	def test_promotion_replaces_the_order_id_placeholder_customer_name(self):
		# Amazon suppresses BuyerName while Pending, so create_customer falls back to the order id.
		placeholder = self.ensure_customer(ORDER_ID)
		repository = self.build_repository()
		with (
			patch.object(repository, "get_order_items", return_value=build_items(self.item_code, 0)),
			patch.object(repository, "create_customer", return_value=placeholder),
			patch.object(repository, "create_address", return_value=None),
		):
			repository.create_sales_order(self.build_order("Pending", 0))

		self.assertEqual(self.get_sales_order("customer_name").customer_name, placeholder)

		self.refresh(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		sales_order = self.get_sales_order("customer", "customer_name")
		self.assertEqual(sales_order.customer, self.customer)
		self.assertEqual(
			sales_order.customer_name,
			self.customer,
			"customer_name must not keep the order-id placeholder after promotion",
		)

	def test_draft_order_qty_is_counted_against_free_stock(self):
		self.assertEqual(get_draft_amazon_order_qty_map([self.item_code]), {})

		repository = self.build_repository()
		self.sync(repository, self.build_order("Pending", 0), rate=0)

		self.assertEqual(
			get_draft_amazon_order_qty_map([self.item_code]).get(self.item_code),
			1.0,
			"a draft reserves nothing in Bin, so it must be deducted or the feed oversells",
		)

	def test_a_submitted_order_is_not_double_counted_against_free_stock(self):
		# Once submitted, erpnext maintains Bin.reserved_qty itself; counting the draft too would
		# understate free stock and suppress listings.
		repository = self.build_repository()
		self.sync(repository, self.build_order("Pending", 0), rate=0)
		self.refresh(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		self.assertEqual(get_draft_amazon_order_qty_map([self.item_code]), {})

	def test_submitted_status_change_never_rebuilds_the_order(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Unshipped", 27.46), rate=27.46)
		before = self.get_sales_order("grand_total")

		existing = repository.get_sales_orders_by_amazon_id([ORDER_ID]).get(ORDER_ID)
		with patch.object(repository, "get_order_items") as mock_get_order_items:
			repository.update_order_status(self.build_order("Shipped", 27.46), existing)

		mock_get_order_items.assert_not_called()
		after = self.get_sales_order("amazon_order_status", "grand_total")
		self.assertEqual(after.amazon_order_status, "Shipped")
		self.assertEqual(after.grand_total, before.grand_total)

	def test_cancel_closes_the_order(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		self.refresh(repository, self.build_order("Canceled", 27.46), rate=27.46)

		sales_order = self.get_sales_order("amazon_order_status", "status")
		self.assertEqual(sales_order.amazon_order_status, "Canceled")
		self.assertEqual(sales_order.status, "Closed")

	def test_cancel_while_still_draft_does_not_close_or_submit(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Pending", 0), rate=0)

		self.refresh(repository, self.build_order("Canceled", 0), rate=0)

		sales_order = self.get_sales_order("docstatus", "amazon_order_status")
		self.assertEqual(sales_order.docstatus, 0)
		self.assertEqual(sales_order.amazon_order_status, "Canceled")

	def test_failed_promotion_leaves_the_draft_and_records_the_failure(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Pending", 0), rate=0)

		existing = repository.get_sales_orders_by_amazon_id([ORDER_ID]).get(ORDER_ID)
		with patch.object(repository, "get_order_items", side_effect=Exception("SP-API throttled")):
			repository.update_order_status(self.build_order("Unshipped", 27.46), existing)

		self.assertEqual(self.get_sales_order("docstatus").docstatus, 0)
		self.assertTrue(frappe.db.exists("Amazon Failed Sync Record", {"amazon_order_id": ORDER_ID}))

	def test_every_status_amazon_can_return_is_a_valid_option(self):
		# The refresh sends no OrderStatuses filter, so Amazon can hand back any status in the
		# lifecycle. db_set bypasses validation, so an unlisted one would be stored silently.
		options = frappe.get_meta("Sales Order").get_field("amazon_order_status").options.split("\n")

		self.assertEqual([status for status in AMAZON_ORDER_STATUSES if status not in options], [])

	def test_partially_shipped_is_recorded_on_a_submitted_order(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		self.refresh(repository, self.build_order("PartiallyShipped", 27.46), rate=27.46)

		self.assertEqual(self.get_sales_order("amazon_order_status").amazon_order_status, "PartiallyShipped")

	def test_pending_availability_is_parked_in_draft_like_pending(self):
		repository = self.build_repository()

		self.sync(repository, self.build_order("PendingAvailability", 0), rate=0)

		self.assertEqual(
			self.get_sales_order("docstatus").docstatus,
			0,
			"a pre-order has no authorised payment either, so it must not submit",
		)

	def test_unchanged_status_is_a_noop(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		self.assertIsNone(self.refresh(repository, self.build_order("Unshipped", 27.46), rate=27.46))

	def test_unknown_order_is_left_to_the_create_path(self):
		repository = self.build_repository()

		self.assertIsNone(repository.update_order_status(self.build_order("Shipped", 27.46), None))


class TestStatusRefreshWindow(IntegrationTestCase):
	def test_window_is_clamped_when_the_watermark_stalls(self):
		run_started_at = now_datetime()
		stalled = add_to_date(run_started_at, days=-30)

		window_start, _ = get_status_refresh_window(stalled, run_started_at)

		floor = add_to_date(run_started_at, hours=-STATUS_REFRESH_MAX_WINDOW_HOURS)
		self.assertEqual(get_datetime(window_start), get_datetime(floor))

	def test_a_backlog_is_walked_in_bounded_slices(self):
		# Closing a slice per run is what stops a truncated pass re-reading page 1 forever.
		run_started_at = now_datetime()
		window_start, window_end = get_status_refresh_window(
			add_to_date(run_started_at, days=-30), run_started_at
		)

		span_hours = (get_datetime(window_end) - get_datetime(window_start)).total_seconds() / 3600
		self.assertEqual(round(span_hours), STATUS_REFRESH_WINDOW_HOURS)
		self.assertLess(get_datetime(window_end), run_started_at)

	def test_a_caught_up_watermark_reads_up_to_now(self):
		run_started_at = now_datetime()
		window_start, window_end = get_status_refresh_window(
			add_to_date(run_started_at, minutes=-20), run_started_at
		)

		self.assertEqual(get_datetime(window_end), run_started_at)
		self.assertLess(get_datetime(window_start), run_started_at)


def init_without_credentials(self):
	# A bare CI site has no SP-API secret, and __init__ reads it eagerly. These tests never make a
	# call, so stand the repository up without one.
	self.refresh_truncated = False
	self.amz_setting = frappe.get_single("Amazon SP API Settings")
	self.instance_params = {}


class TestStatusRefreshPagination(IntegrationTestCase):
	def build_repository(self):
		repository = AmazonRepository.__new__(AmazonRepository)
		repository.amz_setting = frappe.get_single("Amazon SP API Settings")
		repository.refresh_truncated = False
		return repository

	def run_refresh(self, repository, pages):
		# pages is a list of payloads; None simulates call_sp_api_method exhausting its retries.
		calls = iter(pages)

		def fake_call(sp_api_method, **kwargs):
			return next(calls, None)

		with (
			patch.object(repository, "get_orders_instance"),
			patch.object(repository, "call_sp_api_method", side_effect=fake_call),
			patch.object(repository, "update_order_status", return_value=None),
		):
			return repository.refresh_order_statuses(add_to_date(now_datetime(), hours=-1), now_datetime())

	def test_a_clean_run_reads_every_page_and_is_not_truncated(self):
		repository = self.build_repository()
		pages = [{"Orders": [], "NextToken": "t1"}, {"Orders": [], "NextToken": "t2"}, {"Orders": []}]

		result = self.run_refresh(repository, pages)

		self.assertIsNotNone(result)
		self.assertFalse(repository.refresh_truncated)

	def test_a_first_page_failure_returns_none(self):
		repository = self.build_repository()

		self.assertIsNone(self.run_refresh(repository, [None]))

	def test_a_mid_pagination_failure_must_not_look_like_a_clean_finish(self):
		# Regression: call_sp_api_method returns None on exhausted retries. Assigning that into the
		# loop variable ended the walk silently, and the caller then advanced the watermark past
		# pages that were never read — losing those orders from both cursors permanently.
		repository = self.build_repository()
		pages = [{"Orders": [], "NextToken": "t1"}, None]

		self.run_refresh(repository, pages)

		self.assertTrue(
			repository.refresh_truncated,
			"a throttled page must mark the pass truncated so the watermark holds",
		)

	def test_the_page_cap_marks_the_pass_truncated(self):
		repository = self.build_repository()
		pages = [{"Orders": [], "NextToken": f"t{index}"} for index in range(STATUS_REFRESH_MAX_PAGES + 5)]

		with patch.object(amazon_repository, "alert_status_refresh_truncated") as mock_alert:
			self.run_refresh(repository, pages)

		self.assertTrue(repository.refresh_truncated)
		mock_alert.assert_called_once()

	def test_a_truncated_pass_does_not_advance_the_watermark(self):
		settings = frappe.get_single("Amazon SP API Settings")
		watermark = add_to_date(now_datetime(), hours=-2)
		settings.db_set("last_status_synced_at", watermark)

		def truncated_refresh(repository_self, window_start, window_end):
			repository_self.refresh_truncated = True
			return []

		with (
			patch.object(AmazonRepository, "__init__", init_without_credentials),
			patch.object(AmazonRepository, "refresh_order_statuses", autospec=True) as mock_refresh,
		):
			mock_refresh.side_effect = truncated_refresh
			amazon_repository.refresh_order_statuses()

		self.assertEqual(
			get_datetime(frappe.db.get_single_value("Amazon SP API Settings", "last_status_synced_at")),
			get_datetime(watermark),
			"a capped pass must resume from the same point, not skip the remainder",
		)

	def test_a_clean_pass_closes_its_window(self):
		settings = frappe.get_single("Amazon SP API Settings")
		settings.db_set("last_status_synced_at", add_to_date(now_datetime(), hours=-2))

		with (
			patch.object(AmazonRepository, "__init__", init_without_credentials),
			patch.object(AmazonRepository, "refresh_order_statuses", return_value=[]),
		):
			amazon_repository.refresh_order_statuses()

		advanced = get_datetime(frappe.db.get_single_value("Amazon SP API Settings", "last_status_synced_at"))
		self.assertGreater(advanced, add_to_date(now_datetime(), hours=-2))
