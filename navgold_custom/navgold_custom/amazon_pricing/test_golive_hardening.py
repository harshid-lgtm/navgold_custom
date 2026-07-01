# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, change_settings

from navgold_custom.navgold_custom.utils import (
	AmazonFrozenError,
	PricingNotApprovedError,
	block_if_amazon_frozen,
	block_if_pricing_not_approved,
)


def create_active_pricing():
	# asin left unset so Proxy SKU.validate's duplicate-active check never trips.
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.is_active = 1
	proxy_sku.insert()

	pricing = frappe.new_doc("Amazon Product Pricing")
	pricing.proxy_sku = proxy_sku.name
	pricing.is_active = 1
	pricing.insert()
	return pricing.name


class TestGoliveHardening(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.pricing_name = create_active_pricing()

	@change_settings("Navgold Settings", {"freeze_amazon_outbound": 1})
	def test_freeze_blocks_outbound(self):
		self.assertRaises(AmazonFrozenError, block_if_amazon_frozen)

	@change_settings("Navgold Settings", {"freeze_amazon_outbound": 0})
	def test_unfrozen_passes(self):
		block_if_amazon_frozen()

	@change_settings("Navgold Settings", {"pause_strategy_refresh": 1})
	def test_pause_flag_skips_strategy_refresh(self):
		from navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings import (
			schedule_bulk_refresh_strategies,
		)

		with patch("frappe.enqueue") as mock_enqueue:
			schedule_bulk_refresh_strategies()
		mock_enqueue.assert_not_called()

	def test_submit_defers_when_refresh_did_not_complete(self):
		from frappe.utils import add_to_date, now_datetime

		from navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings import (
			schedule_submit_active_pricing,
		)

		now = now_datetime()
		# Last submit is newer than the last refresh -> no fresh refresh this window -> defer.
		with change_settings(
			"Navgold Settings",
			{
				"enable_pricing_sync": 1,
				"amazon_pricing_enabled": 1,
				"last_strategy_refresh_at": add_to_date(now, minutes=-40),
				"last_pricing_submit_at": add_to_date(now, minutes=-10),
			},
		):
			frappe.clear_document_cache("Navgold Settings", "Navgold Settings")
			with patch("frappe.enqueue") as mock_enqueue:
				schedule_submit_active_pricing()
			mock_enqueue.assert_not_called()

	def test_repricer_submit_defers_when_refresh_did_not_complete(self):
		from frappe.utils import add_to_date, now_datetime

		from navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings import (
			schedule_submit_active_repricer,
		)

		now = now_datetime()
		# Last repricer submit is newer than the last refresh -> no fresh refresh this window -> defer.
		with change_settings(
			"Navgold Settings",
			{
				"enable_pricing_sync": 1,
				"repricer_enabled": 1,
				"last_strategy_refresh_at": add_to_date(now, minutes=-40),
				"last_repricer_submit_at": add_to_date(now, minutes=-10),
			},
		):
			frappe.clear_document_cache("Navgold Settings", "Navgold Settings")
			with patch("frappe.enqueue") as mock_enqueue:
				schedule_submit_active_repricer()
			mock_enqueue.assert_not_called()

	def test_repricer_submit_runs_when_refresh_completed_after_last_submit(self):
		from frappe.utils import add_to_date, now_datetime

		from navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings import (
			schedule_submit_active_repricer,
		)

		now = now_datetime()
		with change_settings(
			"Navgold Settings",
			{
				"enable_pricing_sync": 1,
				"repricer_enabled": 1,
				# schedule_submit_active_repricer short-circuits in Dry Run (the bare-site default),
				# so this Live-path stagger-guard test must run in Live mode explicitly.
				"pricing_sync_mode": "Live",
				"last_strategy_refresh_at": add_to_date(now, minutes=-5),
				"last_repricer_submit_at": add_to_date(now, minutes=-20),
			},
		):
			frappe.clear_document_cache("Navgold Settings", "Navgold Settings")
			with patch("frappe.enqueue") as mock_enqueue:
				schedule_submit_active_repricer()
			mock_enqueue.assert_called_once()

	def test_submit_runs_when_refresh_completed_after_last_submit(self):
		from frappe.utils import add_to_date, now_datetime

		from navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings import (
			schedule_submit_active_pricing,
		)

		now = now_datetime()
		# A refresh completed after the last submit window -> safe to submit.
		with change_settings(
			"Navgold Settings",
			{
				"enable_pricing_sync": 1,
				"amazon_pricing_enabled": 1,
				"last_strategy_refresh_at": add_to_date(now, minutes=-5),
				"last_pricing_submit_at": add_to_date(now, minutes=-20),
			},
		):
			frappe.clear_document_cache("Navgold Settings", "Navgold Settings")
			with patch("frappe.enqueue") as mock_enqueue:
				schedule_submit_active_pricing()
			mock_enqueue.assert_called_once()

	@change_settings("Navgold Settings", {"enable_pricing_sync": 0})
	def test_submit_button_requires_pricing_sync_enabled(self):
		pricing = frappe.get_doc("Amazon Product Pricing", self.pricing_name)
		self.assertRaises(frappe.ValidationError, pricing.submit_to_amazon)


class TestApprovalGate(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.pricing_name = create_active_pricing()

	@change_settings("Navgold Settings", {"pricing_sync_mode": "Dry Run"})
	def test_dry_run_blocks_push(self):
		self.assertRaises(PricingNotApprovedError, block_if_pricing_not_approved)

	@change_settings("Navgold Settings", {"pricing_sync_mode": "Live"})
	def test_live_allows_push(self):
		block_if_pricing_not_approved()

	@change_settings("Navgold Settings", {"pricing_sync_mode": "Dry Run", "freeze_amazon_outbound": 0})
	def test_feed_document_chokepoint_blocks_before_http(self):
		from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import Feeds

		client = Feeds.__new__(Feeds)
		# Patch the transport so a guard miss would surface as a network call instead of silently passing.
		with patch.object(Feeds, "post_feed_json") as mock_transport:
			self.assertRaises(PricingNotApprovedError, client.create_feed_document)
		mock_transport.assert_not_called()

	@change_settings("Navgold Settings", {"pricing_sync_mode": "Dry Run", "freeze_amazon_outbound": 0})
	def test_create_feed_chokepoint_blocks_before_http(self):
		from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import Feeds

		client = Feeds.__new__(Feeds)
		with patch.object(Feeds, "post_feed_json") as mock_transport:
			self.assertRaises(
				PricingNotApprovedError,
				client.create_feed,
				feed_type="JSON_LISTINGS_FEED",
				feed_document_id="doc-id",
			)
		mock_transport.assert_not_called()

	@change_settings("Navgold Settings", {"pricing_sync_mode": "Dry Run", "freeze_amazon_outbound": 0})
	def test_repricer_chokepoint_blocks_before_http(self):
		from navgold_custom.navgold_custom.amazon_pricing.repricer_client import RepricerClient

		client = RepricerClient.__new__(RepricerClient)
		client.username = "user"
		client.password = "pass"
		with patch(
			"navgold_custom.navgold_custom.amazon_pricing.repricer_client.make_post_request"
		) as mock_post:
			self.assertRaises(PricingNotApprovedError, client.post_repricer_data, [])
		mock_post.assert_not_called()

	def test_repricer_gate_exception_marks_log_skipped(self):
		from navgold_custom.navgold_custom.amazon_pricing import repricer_client

		pricing_name = self.pricing_name

		with change_settings("Navgold Settings", {"repricer_enabled": 1}):
			# A gate exception at the transport must mark the log "Skipped" (not "Error"), not log_error, not re-raise.
			with (
				patch.object(repricer_client, "collect_pricing_violations", return_value={}),
				patch.object(repricer_client, "partition_pricing_by_price_deviation", return_value=([], [])),
				patch.object(
					repricer_client, "recompute_backorder_prices_with_fallback_supplier", return_value=None
				),
				patch.object(
					repricer_client.RepricerClient,
					"post_repricer_data",
					side_effect=PricingNotApprovedError("Dry Run"),
				),
				patch.object(repricer_client.RepricerClient, "__init__", return_value=None),
				patch("frappe.log_error") as mock_log_error,
			):
				result = repricer_client.sync_to_repricer([pricing_name])

		self.assertTrue(result.get("skipped"))
		log = frappe.get_doc("Amazon Pricing Submission Log", result["log_name"])
		self.assertEqual(log.status, "Skipped")
		mock_log_error.assert_not_called()

	def test_approve_and_revert_flow(self):
		settings = frappe.get_single("Navgold Settings")
		settings.pricing_sync_mode = "Dry Run"
		settings.pricing_sync_approved_by = None
		settings.pricing_sync_approved_at = None
		settings.save()

		settings.approve_and_go_live()
		settings.reload()
		self.assertEqual(settings.pricing_sync_mode, "Live")
		self.assertEqual(settings.pricing_sync_approved_by, frappe.session.user)
		self.assertIsNotNone(settings.pricing_sync_approved_at)

		settings.revert_to_dry_run()
		settings.reload()
		self.assertEqual(settings.pricing_sync_mode, "Dry Run")
		self.assertIsNone(settings.pricing_sync_approved_by)
		self.assertIsNone(settings.pricing_sync_approved_at)


class TestEmptyOrderFailedSyncRecord(IntegrationTestCase):
	def setUp(self):
		self.order_id = "TEST-EMPTY-ORDER-001"
		frappe.db.delete("Amazon Failed Sync Record", {"amazon_order_id": self.order_id})

	def tearDown(self):
		frappe.db.delete("Amazon Failed Sync Record", {"amazon_order_id": self.order_id})

	def build_repository(self):
		from navgold_custom.navgold_custom.amazon_repository import AmazonRepository

		repository = AmazonRepository.__new__(AmazonRepository)
		repository.amz_setting = frappe.get_single("Amazon SP API Settings")
		return repository

	def test_empty_items_writes_single_failed_sync_record(self):
		repository = self.build_repository()
		order = {"AmazonOrderId": self.order_id, "OrderTotal": {"Amount": 12.5}}

		# An order Amazon lists before its items are queryable must be captured, never silently dropped.
		with patch.object(repository, "get_order_items", return_value=[]):
			result = repository.create_sales_order(order)

		self.assertIsNone(result)
		records = frappe.get_all(
			"Amazon Failed Sync Record", filters={"amazon_order_id": self.order_id}, pluck="name"
		)
		self.assertEqual(len(records), 1)

	def test_repeated_empty_items_is_idempotent(self):
		repository = self.build_repository()
		order = {"AmazonOrderId": self.order_id, "OrderTotal": {"Amount": 12.5}}

		with patch.object(repository, "get_order_items", return_value=[]):
			repository.create_sales_order(order)
			repository.create_sales_order(order)

		records = frappe.get_all(
			"Amazon Failed Sync Record", filters={"amazon_order_id": self.order_id}, pluck="name"
		)
		self.assertEqual(len(records), 1)
