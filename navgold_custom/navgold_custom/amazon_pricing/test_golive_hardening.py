# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.tests.utils import change_settings

from navgold_custom.navgold_custom.utils import assert_amazon_outbound_not_frozen


class TestGoliveHardening(IntegrationTestCase):
	@change_settings("Navgold Settings", {"freeze_amazon_outbound": 1})
	def test_freeze_blocks_outbound(self):
		self.assertRaises(frappe.ValidationError, assert_amazon_outbound_not_frozen)

	@change_settings("Navgold Settings", {"freeze_amazon_outbound": 0})
	def test_unfrozen_passes(self):
		assert_amazon_outbound_not_frozen()

	@change_settings("Navgold Settings", {"pause_strategy_refresh": 1})
	def test_pause_flag_skips_strategy_refresh(self):
		from unittest.mock import patch

		from navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings import (
			schedule_bulk_refresh_strategies,
		)

		with patch("frappe.enqueue") as mock_enqueue:
			schedule_bulk_refresh_strategies()
		mock_enqueue.assert_not_called()

	@change_settings("Navgold Settings", {"enable_pricing_sync": 0})
	def test_submit_button_requires_pricing_sync_enabled(self):
		pricing_name = frappe.db.get_value("Amazon Product Pricing", {"is_active": 1})
		if not pricing_name:
			self.skipTest("no pricing fixture available")

		pricing = frappe.get_doc("Amazon Product Pricing", pricing_name)
		self.assertRaises(frappe.ValidationError, pricing.submit_to_amazon)
