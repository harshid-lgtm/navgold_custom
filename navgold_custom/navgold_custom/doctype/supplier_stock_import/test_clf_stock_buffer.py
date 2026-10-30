from frappe.tests import UnitTestCase

from navgold_custom.navgold_custom.doctype.supplier_stock_import.clf_stock_api import (
	apply_clf_stock_buffer,
)


class TestCLFStockBuffer(UnitTestCase):
	def test_below_buffer_becomes_zero(self):
		self.assertEqual(apply_clf_stock_buffer(19, 20), 0)

	def test_equal_to_buffer_keeps_actual_stock(self):
		self.assertEqual(apply_clf_stock_buffer(20, 20), 20)

	def test_above_buffer_keeps_actual_stock(self):
		self.assertEqual(apply_clf_stock_buffer(21, 20), 21)

	def test_zero_buffer_disables_rule(self):
		self.assertEqual(apply_clf_stock_buffer(7, 0), 7)

	def test_negative_values_never_create_negative_stock(self):
		self.assertEqual(apply_clf_stock_buffer(-5, 20), 0)
		self.assertEqual(apply_clf_stock_buffer(10, -3), 10)
