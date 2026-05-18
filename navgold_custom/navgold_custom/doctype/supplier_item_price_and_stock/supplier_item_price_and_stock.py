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
				frappe.throw(_("Supplier not found {0}").format(self.supplier))
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
		if getattr(self.flags, "single_entry", True):
			if frappe.db.exists(
				"Supplier Item Price And Stock",
				{"item": self.item, "supplier": self.supplier, "name": ["!=", self.name]},
			):
				frappe.throw(_("Entry with {0} and {1} already exists").format(self.supplier, self.item))
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
		"navgold_custom.navgold_custom.doctype.supplier_item_price_and_stock.supplier_item_price_and_stock.run_update_supplier_rank",
		now=frappe.flags.in_test,
	)


def run_update_supplier_rank():
	records = frappe.db.get_all(
		"Supplier Item Price and Stock",
		fields=["name", "item", "uom", "price", "purchase_blocked", "supplier"],
	)

	if not records:
		return

	supplier_ranks = {
		supplier.name: supplier.custom_rank or 0
		for supplier in frappe.get_all("Supplier", fields=["name", "custom_rank"])
	}

	groups = defaultdict(list)
	all_rank_updates: dict = {}

	for record in records:
		if record.purchase_blocked:
			all_rank_updates[record.name] = {"rank": 0}
		else:
			record.supplier_custom_rank = supplier_ranks.get(record.supplier, 0)
			groups[record.item].append(record)

	try:
		for group_records in groups.values():
			collect_rank_updates(group_records, all_rank_updates)

		if all_rank_updates:
			frappe.db.bulk_update(
				"Supplier Item Price and Stock",
				all_rank_updates,
				update_modified=False,
			)
	except Exception as error:
		frappe.log_error(title="Error updating supplier rank", message=error)


def collect_rank_updates(group_records, all_updates):
	def sort_key(record):
		price = record.price or 0
		custom_rank = record.supplier_custom_rank or 0
		custom_rank_sort = flt("inf") if custom_rank == 0 else custom_rank
		return (price, custom_rank_sort)

	group_records.sort(key=sort_key)
	rank = 1
	prev_sort_key = None

	for i, record in enumerate(group_records):
		current_sort_key = sort_key(record)

		if prev_sort_key is not None and current_sort_key > prev_sort_key:
			rank = i + 1

		all_updates[record.name] = {"rank": rank}
		prev_sort_key = current_sort_key
