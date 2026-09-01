# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from navgold_custom.navgold_custom import amazon_repository
from navgold_custom.navgold_custom.amazon_repository import (
	AMAZON_ORDER_STATUSES,
	ORDER_SYNC_LAG_MINUTES,
	ORDER_SYNC_MAX_RECOVERED_ORDERS,
	ORDER_SYNC_OVERLAP_MINUTES,
	STATUS_REFRESH_LAG_MINUTES,
	STATUS_REFRESH_MAX_BACKLOG_DAYS,
	STATUS_REFRESH_MAX_PAGES,
	STATUS_REFRESH_OVERLAP_MINUTES,
	STATUS_REFRESH_WINDOW_HOURS,
	AmazonRepository,
	get_order_sync_watermark,
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
		repository.sync_truncated = False
		repository.recovered_order_count = 0
		return repository

	def build_order(self, order_status, amount, fulfillment_channel="MFN"):
		return {
			"AmazonOrderId": ORDER_ID,
			"OrderStatus": order_status,
			"FulfillmentChannel": fulfillment_channel,
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

	def test_an_afn_draft_does_not_eat_merchant_stock(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Pending", 0, fulfillment_channel="AFN"), rate=0)

		self.assertEqual(self.get_sales_order("docstatus", "fulfillment_channel").fulfillment_channel, "AFN")
		self.assertEqual(
			get_draft_amazon_order_qty_map([self.item_code]),
			{},
			"an AFN order ships from Amazon's warehouse, so deducting it hides merchant stock",
		)

	def test_a_submitted_order_is_not_double_counted_against_free_stock(self):
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

	def test_a_cancelled_draft_stops_eating_free_stock(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Pending", 0), rate=0)
		self.assertEqual(get_draft_amazon_order_qty_map([self.item_code]).get(self.item_code), 1.0)

		self.refresh(repository, self.build_order("Canceled", 0), rate=0)

		self.assertEqual(
			get_draft_amazon_order_qty_map([self.item_code]),
			{},
			"a cancelled draft is not demand, and nothing ever submits or deletes it",
		)

	def test_the_sync_never_creates_an_order_amazon_has_already_cancelled(self):
		repository = self.build_repository()

		self.assertIsNone(self.sync(repository, self.build_order("Canceled", 27.46), rate=27.46))
		self.assertFalse(
			frappe.db.exists("Sales Order", {"amazon_order_id": ORDER_ID}),
			"'Canceled' is not an unauthorised status, so an unguarded create would submit it",
		)

	def test_a_cancelled_order_left_open_is_closed_on_the_next_refresh(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Unshipped", 27.46), rate=27.46)
		# the status field is written before the close, so this is what a half-applied cancel leaves
		frappe.db.set_value(
			"Sales Order", self.get_sales_order("name").name, "amazon_order_status", "Canceled"
		)

		self.refresh(repository, self.build_order("Canceled", 27.46), rate=27.46)

		self.assertEqual(
			self.get_sales_order("status").status,
			"Closed",
			"a matching status field must not short-circuit an order that is still open",
		)

	def test_failed_promotion_leaves_the_draft_and_records_the_failure(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Pending", 0), rate=0)

		existing = repository.get_sales_orders_by_amazon_id([ORDER_ID]).get(ORDER_ID)
		with patch.object(repository, "get_order_items", side_effect=Exception("SP-API throttled")):
			repository.update_order_status(self.build_order("Unshipped", 27.46), existing)

		self.assertEqual(self.get_sales_order("docstatus").docstatus, 0)
		self.assertTrue(frappe.db.exists("Amazon Failed Sync Record", {"amazon_order_id": ORDER_ID}))

	def test_every_status_amazon_can_return_is_a_valid_option(self):
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

	def recover(self, repository, order, rate):
		with (
			patch.object(repository, "get_order_items", return_value=build_items(self.item_code, rate)),
			patch.object(repository, "create_customer", return_value=self.customer),
			patch.object(repository, "create_address", return_value=None),
		):
			return repository.sync_order(order, None)

	def test_the_refresh_creates_an_order_the_sync_never_landed(self):
		repository = self.build_repository()

		result = self.recover(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		self.assertTrue(result)
		self.assertEqual(self.get_sales_order("docstatus").docstatus, 1)

	def test_a_recovered_pending_order_is_parked_in_draft(self):
		repository = self.build_repository()

		self.recover(repository, self.build_order("Pending", 0), rate=0)

		sales_order = self.get_sales_order("docstatus", "amazon_order_status")
		self.assertEqual(
			sales_order.docstatus,
			0,
			"Amazon hides the price while Pending, so the recovery path must not submit either",
		)
		self.assertEqual(sales_order.amazon_order_status, "Pending")

	def test_a_recovered_draft_is_counted_against_free_stock(self):
		repository = self.build_repository()

		self.recover(repository, self.build_order("Pending", 0), rate=0)

		self.assertEqual(
			get_draft_amazon_order_qty_map([self.item_code]).get(self.item_code),
			1.0,
			"a recovered draft reserves nothing in Bin, so it must be deducted or the feed oversells",
		)

	def test_an_order_cancelled_in_erpnext_is_not_recreated(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Unshipped", 27.46), rate=27.46)
		frappe.get_doc("Sales Order", self.get_sales_order("name").name).cancel()

		result = self.recover(repository, self.build_order("Shipped", 27.46), rate=27.46)

		self.assertIsNone(result, "a deliberate cancellation must not be undone by the refresh")
		self.assertEqual(
			frappe.db.count("Sales Order", {"amazon_order_id": ORDER_ID}),
			1,
			"recovery must not add a second Sales Order alongside the cancelled one",
		)

	def test_a_resolved_order_that_fails_again_is_reopened_for_triage(self):
		repository = self.build_repository()
		repository.create_failed_sync_record(ORDER_ID, "SP-API throttled")
		frappe.db.set_value(
			"Amazon Failed Sync Record",
			frappe.db.get_value("Amazon Failed Sync Record", {"amazon_order_id": ORDER_ID}),
			"status",
			"Resolved",
		)

		repository.create_failed_sync_record(ORDER_ID, "SP-API throttled again")

		self.assertTrue(
			frappe.db.exists("Amazon Failed Sync Record", {"amazon_order_id": ORDER_ID, "status": "Failed"}),
			"a repeat failure must reopen triage instead of being swallowed by the resolved record",
		)

	def test_an_order_awaiting_triage_is_not_retried(self):
		repository = self.build_repository()
		repository.create_failed_sync_record(ORDER_ID, "SP-API throttled")

		with patch.object(repository, "get_order_items") as mock_get_order_items:
			result = repository.sync_order(self.build_order("Unshipped", 27.46), None)

		self.assertIsNone(result)
		mock_get_order_items.assert_not_called()

	def test_recovery_stops_at_the_per_run_cap(self):
		repository = self.build_repository()
		repository.recovered_order_count = ORDER_SYNC_MAX_RECOVERED_ORDERS

		with patch.object(repository, "get_order_items") as mock_get_order_items:
			result = repository.sync_order(self.build_order("Unshipped", 27.46), None)

		self.assertIsNone(result)
		mock_get_order_items.assert_not_called()
		self.assertFalse(
			frappe.db.exists("Sales Order", {"amazon_order_id": ORDER_ID}),
			"the cap must stop the recovery before it spends any SP-API quota",
		)
		self.assertTrue(
			repository.refresh_truncated,
			"the window must hold, or the orders the cap skipped fall out of it for good",
		)

	def test_the_refresh_does_not_manufacture_an_order_amazon_already_closed(self):
		repository = self.build_repository()

		self.assertIsNone(repository.sync_order(self.build_order("Canceled", 27.46), None))
		self.assertIsNone(self.get_sales_order("name"))

	def test_a_known_order_still_takes_the_status_update_path(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Unshipped", 27.46), rate=27.46)
		existing = repository.get_sales_orders_by_amazon_id([ORDER_ID]).get(ORDER_ID)

		with patch.object(repository, "create_sales_order") as mock_create:
			repository.sync_order(self.build_order("Shipped", 27.46), existing)

		mock_create.assert_not_called()
		self.assertEqual(self.get_sales_order("amazon_order_status").amazon_order_status, "Shipped")

	def fetch_one_order(self, repository, order, rate):
		with (
			patch.object(repository, "get_orders_instance"),
			patch.object(repository, "call_sp_api_method", return_value=order),
			patch.object(repository, "get_order_items", return_value=build_items(self.item_code, rate)),
			patch.object(repository, "create_customer", return_value=self.customer),
			patch.object(repository, "create_address", return_value=None),
		):
			return repository.get_order(ORDER_ID)

	def test_fetching_an_unseen_order_creates_it(self):
		repository = self.build_repository()

		result = self.fetch_one_order(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		self.assertTrue(result)
		self.assertEqual(self.get_sales_order("docstatus").docstatus, 1)

	def test_fetching_a_submitted_order_updates_its_status(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		result = self.fetch_one_order(repository, self.build_order("Shipped", 27.46), rate=27.46)

		self.assertTrue(result)
		self.assertEqual(self.get_sales_order("amazon_order_status").amazon_order_status, "Shipped")

	def test_fetching_a_draft_whose_payment_cleared_promotes_it(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Pending", 0), rate=0)

		self.fetch_one_order(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		sales_order = self.get_sales_order("docstatus", "grand_total")
		self.assertEqual(sales_order.docstatus, 1)
		self.assertGreater(sales_order.grand_total, 0)

	def test_fetching_an_unchanged_order_still_reports_it(self):
		repository = self.build_repository()
		self.sync(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		result = self.fetch_one_order(repository, self.build_order("Unshipped", 27.46), rate=27.46)

		self.assertEqual(result, [self.get_sales_order("name").name])

	def test_refresh_endpoint_rejects_a_non_amazon_order(self):
		sales_order = frappe.new_doc("Sales Order")
		sales_order.customer = self.customer
		sales_order.company = self.settings.company
		sales_order.transaction_date = frappe.utils.nowdate()
		sales_order.delivery_date = frappe.utils.nowdate()
		sales_order.append(
			"items",
			{
				"item_code": self.item_code,
				"qty": 1,
				"rate": 10,
				"warehouse": self.settings.warehouse,
				"delivery_date": frappe.utils.nowdate(),
			},
		)
		sales_order.flags.ignore_mandatory = True
		sales_order.save(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			amazon_repository.refresh_sales_order_status(sales_order.name)

		sales_order.delete(force=True)


class TestStatusRefreshWindow(IntegrationTestCase):
	def test_a_week_old_watermark_is_walked_not_skipped(self):
		run_started_at = now_datetime()
		stalled = add_to_date(run_started_at, days=-7)

		window_start, _, skipped_hours = get_status_refresh_window(stalled, run_started_at)

		self.assertEqual(skipped_hours, 0, "a backlog inside the bound must be walked, not skipped")
		self.assertLess(get_datetime(window_start), add_to_date(run_started_at, days=-6))

	def test_a_watermark_past_the_bound_reports_the_gap(self):
		run_started_at = now_datetime()
		stalled = add_to_date(run_started_at, days=-STATUS_REFRESH_MAX_BACKLOG_DAYS - 5)

		window_start, _, skipped_hours = get_status_refresh_window(stalled, run_started_at)

		self.assertAlmostEqual(skipped_hours, 5 * 24, delta=1)
		floor = add_to_date(run_started_at, days=-STATUS_REFRESH_MAX_BACKLOG_DAYS)
		self.assertEqual(
			get_datetime(window_start),
			get_datetime(add_to_date(floor, minutes=-STATUS_REFRESH_OVERLAP_MINUTES)),
		)

	def test_a_backlog_is_walked_in_bounded_slices(self):
		run_started_at = now_datetime()
		window_start, window_end, _ = get_status_refresh_window(
			add_to_date(run_started_at, days=-3), run_started_at
		)

		span_hours = (get_datetime(window_end) - get_datetime(window_start)).total_seconds() / 3600
		self.assertEqual(round(span_hours), STATUS_REFRESH_WINDOW_HOURS)
		self.assertLess(get_datetime(window_end), run_started_at)

	def test_a_caught_up_watermark_stops_short_of_now(self):
		run_started_at = now_datetime()
		window_start, window_end, skipped_hours = get_status_refresh_window(
			add_to_date(run_started_at, minutes=-20), run_started_at
		)

		self.assertEqual(skipped_hours, 0)
		self.assertEqual(
			get_datetime(window_end), add_to_date(run_started_at, minutes=-STATUS_REFRESH_LAG_MINUTES)
		)
		self.assertLess(get_datetime(window_start), get_datetime(window_end))

	def test_a_blank_watermark_still_yields_a_usable_window(self):
		run_started_at = now_datetime()
		window_start, window_end, skipped_hours = get_status_refresh_window(None, run_started_at)

		self.assertEqual(skipped_hours, 0)
		self.assertLess(get_datetime(window_start), get_datetime(window_end))
		self.assertLessEqual(
			get_datetime(window_end), add_to_date(run_started_at, minutes=-STATUS_REFRESH_LAG_MINUTES)
		)


def init_without_credentials(self):
	self.refresh_truncated = False
	self.sync_truncated = False
	self.amz_setting = frappe.get_single("Amazon SP API Settings")
	self.instance_params = {}


class TestStatusRefreshPagination(IntegrationTestCase):
	def build_repository(self):
		repository = AmazonRepository.__new__(AmazonRepository)
		repository.amz_setting = frappe.get_single("Amazon SP API Settings")
		repository.refresh_truncated = False
		repository.sync_truncated = False
		repository.recovered_order_count = 0
		return repository

	def run_refresh(self, repository, pages):
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


class TestOrderSyncPagination(IntegrationTestCase):
	def build_repository(self):
		repository = AmazonRepository.__new__(AmazonRepository)
		repository.amz_setting = frappe.get_single("Amazon SP API Settings")
		repository.refresh_truncated = False
		repository.sync_truncated = False
		repository.recovered_order_count = 0
		return repository

	def run_sync(self, repository, pages):
		calls = iter(pages)
		self.page_reads = 0

		def fake_call(sp_api_method, **kwargs):
			self.page_reads += 1
			return next(calls, None)

		with (
			patch.object(repository, "get_orders_instance"),
			patch.object(repository, "call_sp_api_method", side_effect=fake_call),
			patch.object(frappe.db, "commit"),
		):
			return repository.get_orders(now_datetime())

	def test_a_clean_run_reads_every_page_and_is_not_truncated(self):
		repository = self.build_repository()
		pages = [{"Orders": [], "NextToken": "t1"}, {"Orders": [], "NextToken": "t2"}, {"Orders": []}]

		result = self.run_sync(repository, pages)

		self.assertIsNotNone(result)
		self.assertFalse(repository.sync_truncated)
		self.assertEqual(self.page_reads, 3)

	def test_an_empty_page_does_not_end_the_run_early(self):
		repository = self.build_repository()
		order_behind_the_gap = {"AmazonOrderId": "TEST-SYNC-BEHIND-EMPTY-PAGE", "OrderStatus": "Unshipped"}
		pages = [{"Orders": [], "NextToken": "t1"}, {"Orders": [order_behind_the_gap]}]

		with patch.object(repository, "create_sales_order", return_value="SO-BEHIND-GAP") as mock_create:
			result = self.run_sync(repository, pages)

		self.assertEqual(
			[call.args[0].get("AmazonOrderId") for call in mock_create.call_args_list],
			[order_behind_the_gap["AmazonOrderId"]],
			"the order behind the empty page must still be read",
		)
		self.assertEqual(result, ["SO-BEHIND-GAP"])

	def test_an_empty_payload_mid_pagination_holds_the_watermark(self):
		repository = self.build_repository()

		self.run_sync(repository, [{"Orders": [], "NextToken": "t1"}, {}])

		self.assertTrue(
			repository.sync_truncated,
			"an empty payload leaves the pages behind it unread, exactly like a failed one",
		)

	def test_a_long_backlog_is_paged_through_rather_than_capped(self):
		repository = self.build_repository()
		backlog = [{"Orders": [], "NextToken": f"t{index}"} for index in range(25)] + [{"Orders": []}]

		self.run_sync(repository, backlog)

		self.assertEqual(self.page_reads, len(backlog), "every page of a backlog must be read")
		self.assertFalse(repository.sync_truncated)

	def test_a_first_page_failure_returns_none(self):
		repository = self.build_repository()

		self.assertIsNone(self.run_sync(repository, [None]))
		self.assertFalse(repository.sync_truncated)

	def test_a_mid_pagination_failure_must_not_look_like_a_clean_finish(self):
		repository = self.build_repository()

		self.run_sync(repository, [{"Orders": [], "NextToken": "t1"}, None])

		self.assertTrue(
			repository.sync_truncated,
			"a throttled page must mark the sync truncated so the watermark holds",
		)


class TestOrderSyncWatermark(IntegrationTestCase):
	def set_watermark(self, watermark):
		frappe.get_single("Amazon SP API Settings").db_set("last_synced_at", watermark)

	def get_watermark(self):
		return get_datetime(frappe.db.get_single_value("Amazon SP API Settings", "last_synced_at"))

	def run_module_sync(self, side_effect, sync_selected_date_only=0):
		with (
			patch.object(AmazonRepository, "__init__", init_without_credentials),
			patch.object(AmazonRepository, "get_orders", autospec=True) as mock_sync,
		):
			mock_sync.side_effect = side_effect
			return amazon_repository.get_orders("2026-07-01", sync_selected_date_only)

	def test_a_clean_run_moves_the_watermark_back_from_the_run_start_not_to_its_end(self):
		self.set_watermark(add_to_date(now_datetime(), hours=-4))
		run_started_at = now_datetime()

		self.run_module_sync(lambda repository, created_after, sync_selected_date_only: [])

		advanced = self.get_watermark()
		self.assertGreater(advanced, add_to_date(run_started_at, hours=-1), "a clean run must advance")
		self.assertLessEqual(
			advanced,
			add_to_date(run_started_at, minutes=-ORDER_SYNC_LAG_MINUTES),
			"the watermark must stay behind now or orders still being indexed are skipped",
		)

	def test_a_truncated_pass_does_not_advance_the_watermark(self):
		watermark = add_to_date(now_datetime(), hours=-4)
		self.set_watermark(watermark)

		def truncated_sync(repository, created_after, sync_selected_date_only):
			repository.sync_truncated = True
			return []

		self.run_module_sync(truncated_sync)

		self.assertEqual(
			self.get_watermark(),
			get_datetime(watermark),
			"pages that were never read must be re-queried, not skipped",
		)

	def test_a_failed_first_page_does_not_advance_the_watermark(self):
		watermark = add_to_date(now_datetime(), hours=-4)
		self.set_watermark(watermark)

		self.run_module_sync(lambda repository, created_after, sync_selected_date_only: None)

		self.assertEqual(self.get_watermark(), get_datetime(watermark))

	def test_a_selected_date_backfill_leaves_the_watermark_alone(self):
		watermark = add_to_date(now_datetime(), hours=-4)
		self.set_watermark(watermark)

		self.run_module_sync(lambda repository, created_after, sync_selected_date_only: [], 1)

		self.assertEqual(
			self.get_watermark(),
			get_datetime(watermark),
			"backfilling one past day must not skip everything between that day and today",
		)
