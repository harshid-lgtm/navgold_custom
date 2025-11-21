# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import DoesNotExistError, _
from frappe.model.document import Document


class SupplierItemPriceandStock(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		currency: DF.Link
		item: DF.Link
		item_price: DF.Link | None
		normalized_qty: DF.Int
		price: DF.Currency
		purchase_blocked: DF.Check
		stock_qty: DF.Int
		supplier: DF.Link
		supplier_part_number: DF.Data | None
		unit_price: DF.Currency
		uom: DF.Link
	# end: auto-generated types

	def before_insert(self):
		self.populate_item_price()

	def calculate_unit_price(self):
		uom_conversion_factor = frappe.db.get_value(
			"UOM Conversion Detail", {"parent": self.item, "uom": self.uom}, "conversion_factor"
		)
		self.unit_price = self.price / uom_conversion_factor

	def calculate_normalized_qty(self):
		uom_conversion_factor = frappe.db.get_value(
			"UOM Conversion Detail", {"parent": self.item, "uom": self.uom}, "conversion_factor"
		)
		self.normalized_qty = self.stock_qty * uom_conversion_factor

	def populate_item_price(self):
		# Assuming we have only one active amz setting at a time..
		amz_setting = frappe.get_last_doc("Amazon SP API Settings", {"is_active": 1})
		try:
			item_price = frappe.get_last_doc(
				"Item Price",
				{
					"item_code": self.item,
					"uom": self.uom,
					"buying": 1,
					"supplier": self.supplier,
					"price_list": amz_setting.price_list,
				},
				order_by="creation",
			)
			item_price.price_list_rate = self.price
			item_price.save()
		except DoesNotExistError:
			if not frappe.db.exists("Supplier", self.supplier):
				frappe.throw(_(f"Supplier not found {self.supplier}"))
			item_price = frappe.get_doc(
				{
					"doctype": "Item Price",
					"item_code": self.item,
					"uom": self.uom,
					"buying": 1,
					"supplier": self.supplier,
					"price_list": amz_setting.price_list,
					"price_list_rate": self.price,
				}
			).insert()
		self.item_price = item_price.name

	def validate(self):
		# Item - Supplier combo should be unique
		if frappe.db.exists(
			"Supplier Item Price And Stock",
			{"item": self.item, "supplier": self.supplier, "name": ["!=", self.name]},
		):
			frappe.throw(_(f"Entry with {self.supplier} and {self.item} already exists"))
		if not self.item_price:
			self.populate_item_price()
		if self.get_value_before_save("price") != self.price:
			amz_setting = frappe.get_last_doc("Amazon SP API Settings", {"is_active": 1})
			item_price = frappe.get_last_doc(
				"Item Price",
				{
					"item_code": self.item,
					"uom": self.uom,
					"buying": 1,
					"supplier": self.supplier,
					"price_list": amz_setting.price_list,
				},
				order_by="creation",
			)
			item_price.price_list_rate = self.price
			item_price.save()
			self.calculate_unit_price()
		if self.get_value_before_save("stock_qty") != self.stock_qty:
			self.calculate_normalized_qty()
