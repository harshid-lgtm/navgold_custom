# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from collections import defaultdict

import frappe
from frappe import DoesNotExistError, _
from frappe.model.document import Document
from frappe.utils.data import flt


class SupplierItemPriceandStock(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from navgold_custom.navgold_custom.doctype.supplier_offer.supplier_offer import SupplierOffer

		barcode: DF.Data | None
		currency: DF.Link
		item: DF.Link
		item_price: DF.Link | None
		offers: DF.Table[SupplierOffer]
		price: DF.Currency
		purchase_blocked: DF.Check
		rank: DF.Int
		stock_qty: DF.Int
		supplier: DF.Link
		supplier_part_number: DF.Data | None
		uom: DF.Link
	# end: auto-generated types

	def before_insert(self):
		if not frappe.db.exists(
			"UOM Conversion Detail", {"parenttype": "Item", "parent": self.item, "uom": self.uom}
		):
			self.create_item_uom()
		self.populate_item_price()

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
		if getattr(self.flags, "single_entry", True):
			if frappe.db.exists(
				"Supplier Item Price And Stock",
				{"item": self.item, "supplier": self.supplier, "name": ["!=", self.name]},
			):
				frappe.throw(_(f"Entry with {self.supplier} and {self.item} already exists"))
		if not self.item_price:
			self.populate_item_price()
		if self.get_value_before_save("price") != self.price:
			frappe.db.set_value("Item Price", self.item_price, "price_list_rate", self.price)

	def create_item_uom(self):
		item = frappe.get_cached_doc("Item", self.item)
		item.append(
			"uoms",
			{
				"uom": self.uom,
				"conversion_factor": frappe.db.get_value("UOM", self.uom, "custom_default_conversion_factor"),
			},
		).save()

	def on_update(self):
		if not getattr(self.flags, "bulk_entry", False):
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
		fields=["name", "item", "uom", "price", "purchase_blocked", "supplier"],
	)

	if not records:
		return

	supplier_ranks = {}
	supplier_ranks = {
		s.name: s.custom_rank or 0 for s in frappe.get_all("Supplier", fields=["name", "custom_rank"])
	}

	groups = defaultdict(list)
	blocked = []

	for record in records:
		if record.purchase_blocked:
			blocked.append(record.name)
		else:
			record.supplier_custom_rank = supplier_ranks.get(record.supplier, 0)
			groups[(record.item)].append(record)

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
	"""
	Rank suppliers by price, using supplier's custom_rank as tiebreaker.

	Sorting logic:
	- Primary: price (ascending, lowest price = best rank)
	- Secondary (tiebreaker): supplier_custom_rank (ascending, but 0 goes last)

	For ties handling: if sorted prices are [10, 10, 20, 30, 30], ranks are [1, 1, 2, 3, 3]
	With custom_rank tiebreaker, same-price items with different custom_ranks get sequential ranks.
	"""

	def sort_key(record):
		price = record.price or 0
		custom_rank = record.supplier_custom_rank or 0
		custom_rank_sort = flt("inf") if custom_rank == 0 else custom_rank
		return (price, custom_rank_sort)

	group_records.sort(key=sort_key)
	doc_updates = {}
	rank = 1
	prev_sort_key = None

	for i, record in enumerate(group_records):
		current_sort_key = sort_key(record)

		if prev_sort_key is not None and current_sort_key > prev_sort_key:
			rank = i + 1

		doc_updates[record.name] = {"rank": rank}
		prev_sort_key = current_sort_key

	frappe.db.bulk_update(
		"Supplier Item Price and Stock",
		doc_updates,
		update_modified=False,
	)
