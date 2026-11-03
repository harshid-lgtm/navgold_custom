import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.patches.v1.backfill_sales_order_shipping_search_fields import (
	SHIPPING_SEARCH_FIELDS,
	ensure_shipping_search_fields,
)


class TestShippingBackfillPatch(IntegrationTestCase):
	def test_patch_creates_required_sales_order_fields_before_backfill(self):
		ensure_shipping_search_fields()

		for fieldname in SHIPPING_SEARCH_FIELDS:
			self.assertTrue(
				frappe.db.exists(
					"Custom Field",
					{"dt": "Sales Order", "fieldname": fieldname},
				)
			)
