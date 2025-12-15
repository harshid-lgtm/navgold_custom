# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from collections import defaultdict

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
		rank: DF.Int
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
		price_list = frappe.db.get_single_value("Navgold Settings", "buying_price_list")
		try:
			item_price = frappe.get_last_doc(
				"Item Price",
				{
					"item_code": self.item,
					"uom": self.uom,
					"buying": 1,
					"supplier": self.supplier,
					"price_list": price_list,
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
					"price_list": price_list,
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
			price_list = frappe.db.get_single_value("Navgold Settings", "buying_price_list")

			item_price = frappe.get_last_doc(
				"Item Price",
				{
					"item_code": self.item,
					"uom": self.uom,
					"buying": 1,
					"supplier": self.supplier,
					"price_list": price_list,
				},
				order_by="creation",
			)
			item_price.price_list_rate = self.price
			item_price.save()
			self.calculate_unit_price()
		if self.get_value_before_save("stock_qty") != self.stock_qty:
			self.calculate_normalized_qty()

	def on_update(self):
		if (
			self.get_value_before_save("price") != self.price
			or self.get_value_before_save("purchase_blocked") != self.purchase_blocked
		):
			update_supplier_rank()


@frappe.whitelist()
def update_supplier_rank():
	frappe.enqueue(
		"navgold_custom.navgold_custom.doctype.supplier_item_price_and_stock.supplier_item_price_and_stock._update_supplier_rank",
		now=True,
	)


def _update_supplier_rank():
	records = frappe.db.get_all(
		"Supplier Item Price and Stock",
		fields=["name", "item", "uom", "unit_price", "purchase_blocked"],
	)

	if not records:
		return

	groups = defaultdict(list)
	blocked = []

	for record in records:
		if record.purchase_blocked:
			blocked.append(record.name)
		else:
			groups[(record.item, record.uom)].append(record)

	try:
		if blocked:
			frappe.db.set_value(
				"Supplier Item Price and Stock",
				{"name": ["in", blocked]},
				"rank",
				0,
			)

		for group_records in groups.values():
			update_rank_by_unit_price(group_records)
	except Exception as e:
		frappe.log_error(title="Error updating supplier rank", message=e)


def update_rank_by_unit_price(group_records):
	"""After sorting we use for loop to handle ties, if the sorted list is [10, 10, 20, 30, 30], ranks should be [1, 1, 2, 3, 3] instead of [1, 2, 3, 4, 5]"""

	group_records.sort(key=lambda x: x.unit_price or 0)
	doc_updates = {}
	rank = 1
	prev_price = None

	for i, record in enumerate(group_records):
		price = record.unit_price or 0

		if prev_price is not None and price > prev_price:
			rank = i + 1

		doc_updates[record.name] = {"rank": rank}
		prev_price = price

	frappe.db.bulk_update(
		"Supplier Item Price and Stock",
		doc_updates,
		update_modified=False,
	)
