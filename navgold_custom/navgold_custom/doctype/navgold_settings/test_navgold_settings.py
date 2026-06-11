# Copyright (c) 2025, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import patch

from frappe.tests import IntegrationTestCase, change_settings

from navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings import (
	schedule_bulk_fetch_afn_stock,
)


class IntegrationTestNavgoldSettings(IntegrationTestCase):
	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 0})
	def test_afn_stock_sync_disabled_does_not_enqueue(self):
		with patch("frappe.enqueue") as mock_enqueue:
			schedule_bulk_fetch_afn_stock()
		mock_enqueue.assert_not_called()

	@change_settings("Navgold Settings", {"enable_afn_stock_sync": 1})
	def test_afn_stock_sync_enabled_enqueues_with_shared_job_id(self):
		with patch("frappe.enqueue") as mock_enqueue:
			schedule_bulk_fetch_afn_stock()

		mock_enqueue.assert_called_once()
		kwargs = mock_enqueue.call_args.kwargs
		# Must match the manual entry's job_id so manual + scheduled runs never double-pull.
		self.assertEqual(kwargs["job_id"], "navgold.scheduled_bulk_fetch_afn_stock")
		self.assertTrue(kwargs["deduplicate"])
		self.assertEqual(
			kwargs["method"],
			"navgold_custom.navgold_custom.amazon_pricing.afn_stock_fetcher.process_afn_stock_fetch",
		)
