# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import copy

from frappe.tests import UnitTestCase

from navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip_api import (
	build_document_spec_from_rate,
	extract_label_from_purchase,
)


def build_spec(document_format, mandatory_types, size=None, dpis=None):
	return {
		"format": document_format,
		"size": size or {"length": 6.0, "unit": "INCH", "width": 4.0},
		"printOptions": [
			{
				"supportedDPIs": dpis or [],
				"supportedPageLayouts": ["LEFT"],
				# every UK carrier returns [True] only — joining is never optional
				"supportedFileJoiningOptions": [True],
				"supportedDocumentDetails": [{"name": name, "isMandatory": True} for name in mandatory_types],
			}
		],
	}


# captured from a live getRates for MAT-PAC-2026-00018; Amazon names the document PACKSLIP
EVRI_RATE = {
	"rateId": "RATE-EVRI",
	"carrierName": "Hermes UK",
	"supportedDocumentSpecifications": [
		build_spec("PDF", ["PACKSLIP", "LABEL"]),
		build_spec("ZPL", ["LABEL"], dpis=[300]),
		build_spec("PNG", ["LABEL"]),
		build_spec("PDF", ["PACKSLIP", "LABEL"], size={"length": 11.0, "unit": "INCH", "width": 8.5}),
	],
}

AMAZON_SHIPPING_RATE = {
	"rateId": "RATE-AMZN",
	"carrierName": "Amazon Shipping",
	"supportedDocumentSpecifications": [
		build_spec("PDF", ["LABEL"]),
		build_spec("ZPL", ["LABEL"], dpis=[300]),
		build_spec("PNG", ["LABEL"]),
	],
}


class TestSFPDocumentSpec(UnitTestCase):
	def test_evri_asks_for_the_label_alone(self):
		# the PDF spec mandates PACKSLIP too, so the label-only PNG is the one to ask for
		spec = build_document_spec_from_rate(EVRI_RATE)
		self.assertEqual(spec["requestedDocumentTypes"], ["LABEL"])
		self.assertEqual(spec["format"], "PNG")

	def test_a_label_only_pdf_is_preferred_when_the_carrier_offers_one(self):
		spec = build_document_spec_from_rate(AMAZON_SHIPPING_RATE)
		self.assertEqual(spec["requestedDocumentTypes"], ["LABEL"])
		self.assertEqual(spec["format"], "PDF")

	def test_the_label_size_comes_from_the_selected_spec(self):
		spec = build_document_spec_from_rate(EVRI_RATE)
		self.assertEqual(spec["size"], {"length": 6.0, "unit": "INCH", "width": 4.0})

	def test_a_carrier_with_no_label_only_spec_still_returns_a_pdf(self):
		rate = copy.deepcopy(EVRI_RATE)
		rate["supportedDocumentSpecifications"] = [build_spec("PDF", ["PACKSLIP", "LABEL"])]

		spec = build_document_spec_from_rate(rate)

		self.assertEqual(spec["format"], "PDF")
		self.assertEqual(spec["requestedDocumentTypes"], ["PACKSLIP", "LABEL"])

	def test_only_the_label_document_is_kept(self):
		purchase_payload = {
			"packageDocumentDetails": [
				{
					"trackingId": "H0001",
					"packageDocuments": [
						{"type": "PACKSLIP", "format": "PDF", "contents": "cGFja2luZw=="},
						{"type": "LABEL", "format": "PNG", "contents": "bGFiZWw="},
					],
				}
			]
		}

		label_content, label_format, tracking_id = extract_label_from_purchase(purchase_payload)

		self.assertEqual(label_content, b"label")
		self.assertEqual(label_format, "PNG")
		self.assertEqual(tracking_id, "H0001")
