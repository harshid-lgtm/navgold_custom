# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import SHIPPING_GROUPS
from navgold_custom.navgold_custom.report.amazon_pricing_feed_preview import (
	amazon_pricing_feed_preview as feed_preview,
)


def shipping_group_message(group_uuid):
	return {
		"sku": "TEST-SKU",
		"patches": [
			{
				"op": "replace",
				"path": "/attributes/merchant_shipping_group",
				"value": [{"value": group_uuid, "marketplace_id": "A1F83G8C2ARO7P"}],
			}
		],
	}


class TestFeedPreviewShippingGroup(IntegrationTestCase):
	def test_known_shipping_groups_map_to_labels(self):
		expected = {
			SHIPPING_GROUPS["sfp"]: "SFP",
			SHIPPING_GROUPS["main_revised"]: "Main Revised",
			SHIPPING_GROUPS["two_day"]: "Two-Day",
		}
		for group_uuid, label in expected.items():
			values = feed_preview.extract_amazon_values(shipping_group_message(group_uuid))
			self.assertEqual(values["shipping_group"], label)

	def test_unknown_shipping_group_falls_back_to_raw_value(self):
		values = feed_preview.extract_amazon_values(shipping_group_message("unmapped-uuid-123"))
		self.assertEqual(values["shipping_group"], "unmapped-uuid-123")

	def test_no_shipping_group_patch_yields_none(self):
		# An FBA message carries no merchant_shipping_group patch.
		values = feed_preview.extract_amazon_values({"sku": "TEST-SKU", "patches": []})
		self.assertIsNone(values["shipping_group"])
