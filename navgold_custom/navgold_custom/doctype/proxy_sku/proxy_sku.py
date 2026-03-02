# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import DocType


class ProxySKU(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		asin: DF.Link | None
		is_active: DF.Check
		is_fba: DF.Check
		item: DF.Link | None
		last_transfer_rate: DF.Currency
		naming_series: DF.Data | None
		uom: DF.Link | None
	# end: auto-generated types

	def autoname(self):
		# Allow manually set name to persist
		if getattr(self, "_user_provided_name", None):
			self.name = self._user_provided_name
			return

		# fallback to naming series
		if not self.naming_series:
			self.naming_series = f"{self.item}-{self.uom}-.####"

		self.name = frappe.model.naming.make_autoname(self.naming_series)

	def before_insert(self):
		# Preserve user-specified name before frappe resets it
		if self.name and self.name != f"New {self.doctype}":
			self._user_provided_name = self.name

	def validate(self):
		multiple_active_item_uom = frappe.get_single_value("Navgold Settings", "multiple_active_item_uom")
		if multiple_active_item_uom:
			return
		existing = frappe.db.exists(
			"Proxy SKU",
			{
				"item": self.item,
				"uom": self.uom,
				"is_active": 1,
				"name": ["!=", self.name],
				"asin": ["!=", None],
			},
		)
		if existing:
			frappe.throw(
				_(f"Active Proxy SKU for Item {self.item} with UOM {self.uom} already exists: {existing}"),
				title=_("Duplicate Proxy SKU"),
			)

	@frappe.whitelist()
	def create_next_proxy_sku(self):
		self.is_active = 0
		self.save()

		doc = frappe.new_doc("Proxy SKU")
		doc.update(
			{
				"item": self.item,
				"uom": self.uom,
				"asin": self.asin,
				"is_active": 1,
			}
		)
		return doc.insert(ignore_permissions=True)


def get_proxy_sku_map(sku_names=None):
	proxy_sku = DocType("Proxy SKU")
	query = (
		frappe.qb.from_(proxy_sku)
		.select(proxy_sku.name, proxy_sku.item, proxy_sku.asin, proxy_sku.uom, proxy_sku.is_fba)
		.where(proxy_sku.is_active == 1)
	)

	if sku_names:
		query = query.where(proxy_sku.name.isin(sku_names))

	rows = query.run(as_dict=True)

	return {
		row["name"]: {
			"item": row["item"],
			"asin": row["asin"] or "",
			"uom": row["uom"],
			"is_fba": row["is_fba"],
		}
		for row in rows
	}
