# Copyright (c) 2026, Navgold Ltd and contributors

import unittest

from navgold_custom.navgold_custom.doctype.supplier_stock_import.suma_stock_import import (
	apply_suma_stock_buffer,
	build_raw_indexes,
	detect_columns,
	normalize_barcode,
	normalize_supplier_code,
)


class TestSumaStockImport(unittest.TestCase):
	def test_buffer_zeroes_stock_below_threshold(self):
		self.assertEqual(apply_suma_stock_buffer(30, 50), 0)
		self.assertEqual(apply_suma_stock_buffer(49, 50), 0)
		self.assertEqual(apply_suma_stock_buffer(50, 50), 50)
		self.assertEqual(apply_suma_stock_buffer(75, 50), 75)

	def test_zero_buffer_disables_threshold(self):
		self.assertEqual(apply_suma_stock_buffer(30, 0), 30)

	def test_known_stock_header_variants(self):
		for header in ("Stock", "BALANCE", "Qty", "Quantity", "Stock No"):
			columns = detect_columns(["PLCODE", "BarcodeINNER", header])
			self.assertEqual(columns["stock"], 2)

	def test_supplier_code_is_primary_index(self):
		columns = detect_columns(["PLCODE", "BarcodeINNER", "Stock"])
		by_supplier_code, by_barcode, _, _ = build_raw_indexes(
			[["SUP-1", "0501234567890", 75]],
			columns,
		)
		self.assertEqual(by_supplier_code["SUP-1"]["stock"], 75)
		self.assertEqual(by_barcode["0501234567890"]["supplier_code"], "SUP-1")

	def test_normalization(self):
		self.assertEqual(normalize_supplier_code(" 1234 "), "1234")
		self.assertEqual(normalize_supplier_code("1234.0"), "1234")
		self.assertEqual(normalize_barcode(" 0501234567890 "), "0501234567890")


if __name__ == "__main__":
	unittest.main()
