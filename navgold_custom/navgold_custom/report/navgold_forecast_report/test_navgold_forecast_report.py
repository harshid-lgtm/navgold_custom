# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import make_bundle, make_item
from navgold_custom.navgold_custom.report.navgold_forecast_report.navgold_forecast_report import (
	get_transfer_rows,
)


class TestGetTransferRows(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		suffix = frappe.generate_hash(length=6)

		cls.plain_item = make_item(f"NG-PLAIN-{suffix}", is_stock_item=1)
		cls.component_a = make_item(f"NG-COMP-A-{suffix}", is_stock_item=1)
		cls.component_b = make_item(f"NG-COMP-B-{suffix}", is_stock_item=1)
		cls.bundle_item = make_item(f"NG-BUNDLE-{suffix}", is_stock_item=0)
		make_bundle(cls.bundle_item, [(cls.component_a, 2), (cls.component_b, 3)])

	def test_non_bundle_item_stays_a_single_row(self):
		rows = get_transfer_rows(self.plain_item, "Nos", 5)
		self.assertEqual(rows, [{"item_code": self.plain_item, "qty": 5, "uom": "Nos"}])

	def test_bundle_splits_into_components_with_scaled_qty(self):
		rows = get_transfer_rows(self.bundle_item, "Nos", 4)
		self.assertEqual(
			rows,
			[
				{"item_code": self.component_a, "qty": 8},
				{"item_code": self.component_b, "qty": 12},
			],
		)
