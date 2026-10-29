from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.custom_script.purchase_order.purchase_order import (
	get_supplier_part_number_value,
	set_supplier_part_no,
)


class TestPurchaseOrderSupplierCode(IntegrationTestCase):
	def test_exact_supplier_item_uom_match(self):
		rows = [
			frappe._dict(
				name="SUP-ROW-1",
				supplier_part_number="HLL4",
				rank=2,
				modified="2026-10-29 10:00:00",
			)
		]
		with patch("frappe.get_all", return_value=rows) as get_all:
			result = get_supplier_part_number_value("CLF Distribution", "15696", "Nos")

		self.assertEqual(result, "HLL4")
		get_all.assert_called_once_with(
			"Supplier Item Price and Stock",
			filters={
				"supplier": "CLF Distribution",
				"item": "15696",
				"uom": "Nos",
				"supplier_part_number": ["is", "set"],
			},
			fields=["name", "supplier_part_number", "rank", "modified"],
			order_by="modified desc",
		)

	def test_lowest_positive_rank_wins(self):
		rows = [
			frappe._dict(
				name="SUP-ROW-2",
				supplier_part_number="SECOND",
				rank=2,
				modified="2026-10-29 12:00:00",
			),
			frappe._dict(
				name="SUP-ROW-1",
				supplier_part_number="FIRST",
				rank=1,
				modified="2026-10-29 11:00:00",
			),
			frappe._dict(
				name="SUP-ROW-0",
				supplier_part_number="UNRANKED",
				rank=0,
				modified="2026-10-29 13:00:00",
			),
		]
		with patch("frappe.get_all", return_value=rows):
			self.assertEqual(
				get_supplier_part_number_value("CLF Distribution", "15696", "Nos"),
				"FIRST",
			)

	def test_no_match_returns_blank(self):
		with patch("frappe.get_all", return_value=[]):
			self.assertEqual(
				get_supplier_part_number_value("CLF Distribution", "NO-MATCH", "Nos"),
				"",
			)

	def test_server_hook_clears_stale_value_when_no_match(self):
		doc = frappe._dict(
			supplier="CLF Distribution",
			items=[
				frappe._dict(
					item_code="NO-MATCH",
					uom="Nos",
					supplier_part_no="OLD-CODE",
				)
			],
		)
		with patch(
			"navgold_custom.navgold_custom.custom_script.purchase_order.purchase_order.get_supplier_part_number_value",
			return_value="",
		):
			set_supplier_part_no(doc)

		self.assertEqual(doc.items[0].supplier_part_no, "")
