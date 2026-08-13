# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import copy

from frappe.tests import UnitTestCase

from navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip_api import (
	build_document_spec_from_rate,
	extract_label_from_purchase,
)

EVRI_RATE = {
	"rateId": "RATE-EVRI",
	"carrierName": "Evri",
	"supportedDocumentSpecifications": [
		{
			"format": "PDF",
			"size": {"length": 6.0, "unit": "INCH", "width": 4.0},
			"printOptions": [
				{
					"supportedDPIs": [300],
					"supportedPageLayouts": ["DEFAULT"],
					"supportedFileJoiningOptions": [True, False],
					"supportedDocumentDetails": [
						{"name": "LABEL", "isMandatory": True},
						{"name": "PACKING_SLIP", "isMandatory": True},
					],
				}
			],
		}
	],
}


class TestSFPDocumentSpec(UnitTestCase):
	def test_label_is_not_joined_with_packing_slip(self):
		spec = build_document_spec_from_rate(EVRI_RATE)
		self.assertFalse(spec["needFileJoining"])

	def test_joining_stays_on_when_carrier_offers_no_alternative(self):
		rate = copy.deepcopy(EVRI_RATE)
		rate["supportedDocumentSpecifications"][0]["printOptions"][0]["supportedFileJoiningOptions"] = [True]
		self.assertTrue(build_document_spec_from_rate(rate)["needFileJoining"])

	def test_only_the_label_document_is_kept(self):
		purchase_payload = {
			"packageDocumentDetails": [
				{
					"trackingId": "H0001",
					"packageDocuments": [
						{"type": "PACKING_SLIP", "format": "PDF", "contents": "cGFja2luZw=="},
						{"type": "LABEL", "format": "PDF", "contents": "bGFiZWw="},
					],
				}
			]
		}
		label_content, label_format, tracking_id = extract_label_from_purchase(purchase_payload)
		self.assertEqual(label_content, b"label")
		self.assertEqual(label_format, "PDF")
		self.assertEqual(tracking_id, "H0001")
