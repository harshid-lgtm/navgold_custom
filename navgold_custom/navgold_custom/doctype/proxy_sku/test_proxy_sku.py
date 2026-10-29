# Copyright (c) 2025, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku import (
	_format_amazon_listing_content,
	get_amazon_listing_content_by_asin,
)


class TestProxySKU(FrappeTestCase):
	def test_format_amazon_listing_content(self):
		payload = {
			"asin": "B0TEST12345",
			"summaries": [
				{
					"asin": "B0TEST12345",
					"itemName": "Amazon Live Title",
					"brand": "Example Brand",
					"productType": "FOOD",
				}
			],
			"attributes": {
				"product_description": [{"value": "Live product description"}],
				"bullet_point": [
					{"value": "Bullet one"},
					{"value": "Bullet two"},
				],
			},
			"status": ["BUYABLE"],
		}

		result = _format_amazon_listing_content(
			payload,
			seller_skus=["SKU-1"],
			source="Listings Items API",
		)

		self.assertEqual(result["seller_skus"], ["SKU-1"])
		self.assertEqual(result["asin"], "B0TEST12345")
		self.assertEqual(result["title"], "Amazon Live Title")
		self.assertEqual(result["description"], "Live product description")
		self.assertEqual(result["bullet_points"], ["Bullet one", "Bullet two"])
		self.assertEqual(result["brand"], "Example Brand")
		self.assertEqual(result["product_type"], "FOOD")
		self.assertEqual(result["status"], ["BUYABLE"])

	def test_proxy_sku_lookup_uses_listings_api_without_writing(self):
		doc = frappe.get_doc(
			{
				"doctype": "Proxy SKU",
				"name": "SKU-1",
				"item": "ITEM-1",
				"uom": "Nos",
				"asin": "B0TEST12345",
			}
		)
		client = MagicMock()
		client.get_listing_item.return_value = {
			"sku": "SKU-1",
			"summaries": [{"asin": "B0TEST12345", "itemName": "Listing Title"}],
			"attributes": {
				"product_description": [{"value": "Listing description"}],
				"bullet_point": [{"value": "Listing bullet"}],
			},
		}

		with (
			patch(
				"navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku.Listings.from_amazon_settings",
				return_value=client,
			),
			patch.object(doc, "save") as save,
		):
			result = doc.get_amazon_listing_content()

		client.get_listing_item.assert_called_once_with(
			sku="SKU-1",
			included_data="summaries,attributes",
		)
		save.assert_not_called()
		self.assertEqual(result["title"], "Listing Title")
		self.assertEqual(result["description"], "Listing description")
		self.assertEqual(result["bullet_points"], ["Listing bullet"])

	def test_asin_lookup_uses_catalog_api_without_writing(self):
		catalog_payload = {
			"asin": "B0TEST12345",
			"summaries": [{"itemName": "Catalog Title", "brand": "Example Brand"}],
			"attributes": {
				"product_description": [{"value": "Catalog description"}],
				"bullet_point": [{"value": "Catalog bullet"}],
			},
		}
		client = MagicMock()
		client.get_catalog_item.return_value = catalog_payload

		with (
			patch.object(frappe, "get_all", return_value=["SKU-1", "SKU-2"]) as get_all,
			patch(
				"navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku.CatalogItems.from_amazon_settings",
				return_value=client,
			),
			patch.object(frappe.db, "set_value") as set_value,
		):
			result = get_amazon_listing_content_by_asin("B0TEST12345")

		get_all.assert_called_once_with("Proxy SKU", filters={"asin": "B0TEST12345"}, pluck="name")
		client.get_catalog_item.assert_called_once_with(
			"B0TEST12345",
			included_data="summaries,attributes,productTypes",
		)
		set_value.assert_not_called()
		self.assertEqual(result["seller_skus"], ["SKU-1", "SKU-2"])
		self.assertEqual(result["title"], "Catalog Title")
		self.assertEqual(result["description"], "Catalog description")
		self.assertEqual(result["bullet_points"], ["Catalog bullet"])

	def test_asin_lookup_rejects_unlinked_asin(self):
		with patch.object(frappe, "get_all", return_value=[]):
			with self.assertRaises(frappe.ValidationError):
				get_amazon_listing_content_by_asin("B0NOTINERP")
