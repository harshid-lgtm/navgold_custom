# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_repository import AmazonRepository, format_local_as_amazon_utc


class TestFormatLocalAsAmazonUtc(IntegrationTestCase):
	def test_summer_offset_is_applied(self):
		# Europe/London in June is BST (UTC+1): 09:00 local -> 08:00 UTC
		with self.change_settings("System Settings", time_zone="Europe/London"):
			self.assertEqual(
				format_local_as_amazon_utc("2026-06-10 09:00:00"),
				"2026-06-10T08:00:00Z",
			)

	def test_winter_has_no_offset(self):
		# Europe/London in January is GMT (UTC+0): 09:00 local -> 09:00 UTC
		with self.change_settings("System Settings", time_zone="Europe/London"):
			self.assertEqual(
				format_local_as_amazon_utc("2026-01-10 09:00:00"),
				"2026-01-10T09:00:00Z",
			)

	def test_date_only_input_starts_at_local_midnight(self):
		# A bare date is local midnight; BST pushes it to 23:00 UTC of the previous day,
		# so the selected-day window still covers the full London day.
		with self.change_settings("System Settings", time_zone="Europe/London"):
			self.assertEqual(
				format_local_as_amazon_utc("2026-06-10"),
				"2026-06-09T23:00:00Z",
			)

	def test_non_london_timezone(self):
		# Asia/Kolkata is UTC+5:30: 09:00 local -> 03:30 UTC
		with self.change_settings("System Settings", time_zone="Asia/Kolkata"):
			self.assertEqual(
				format_local_as_amazon_utc("2026-06-10 09:00:00"),
				"2026-06-10T03:30:00Z",
			)


class TestGetOrderItems(IntegrationTestCase):
	def test_raises_when_sp_api_call_fails(self):
		# A throttled/failed items call (payload is None) must raise, not return [] — otherwise
		# the order is silently dropped with no Failed Sync Record.
		repository = AmazonRepository.__new__(AmazonRepository)
		repository.amz_setting = frappe._dict(warehouse="_Test Warehouse")
		with (
			patch.object(repository, "get_orders_instance", return_value=None),
			patch.object(repository, "call_sp_api_method", return_value=None),
		):
			with self.assertRaises(Exception):
				repository.get_order_items("TEST-ORDER-ID")

	def test_raises_when_pagination_call_fails(self):
		# A throttle on the NextToken re-fetch leaves the order partially read — it must fail,
		# not build an incomplete item list.
		repository = AmazonRepository.__new__(AmazonRepository)
		repository.amz_setting = frappe._dict(warehouse="_Test Warehouse")
		first_page = {"OrderItems": [], "NextToken": "TOKEN"}
		with (
			patch.object(repository, "get_orders_instance", return_value=None),
			patch.object(repository, "call_sp_api_method", side_effect=[first_page, None]),
		):
			with self.assertRaises(Exception):
				repository.get_order_items("TEST-ORDER-ID")
