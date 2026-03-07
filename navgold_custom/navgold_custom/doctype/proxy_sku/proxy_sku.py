# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import DocType

from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import (
	FBAInventory,
	Listings,
)


class ProxySKU(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		asin: DF.Link | None
		fnsku: DF.Data | None
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
	def fetch_asin(self):
		listing = Listings.from_amazon_settings().fetch_for_sku(self.name)
		if not listing:
			frappe.throw(_("No listing data found for SKU {0}").format(self.name))

		asin_value = listing.get("asin")
		if not asin_value:
			frappe.throw(_("No ASIN found in listing for SKU {0}").format(self.name))

		if not frappe.db.exists("Amazon ASIN", asin_value):
			asin_doc = frappe.new_doc("Amazon ASIN")
			asin_doc.asin = asin_value
			asin_doc.insert(ignore_permissions=True)

		self.asin = asin_value
		self.save()
		return asin_value

	@frappe.whitelist()
	def fetch_fnsku(self):
		if not self.is_fba:
			frappe.throw(_("FNSKU can only be fetched for FBA Proxy SKUs"))

		results = FBAInventory.from_amazon_settings().fetch_for_skus([self.name])
		summary = results.get(self.name)
		if not summary:
			frappe.throw(_("No FBA inventory data found for SKU {0}").format(self.name))

		self.fnsku = summary.get("fnSku")
		self.save()
		return self.fnsku

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


@frappe.whitelist()
def bulk_fetch_fnsku():
	proxy_skus = frappe.get_all(
		"Proxy SKU",
		filters={"is_fba": 1, "is_active": 1, "fnsku": ["in", ["", None]]},
		pluck="name",
	)

	if not proxy_skus:
		return {"updated": 0, "failed": 0, "message": "No FBA Proxy SKUs with missing FNSKU found"}

	frappe.enqueue(
		"navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku.process_bulk_fnsku_fetch",
		seller_skus=proxy_skus,
		queue="long",
	)

	return {"message": f"Queued {len(proxy_skus)} Proxy SKUs for FNSKU fetch"}


def process_bulk_fnsku_fetch(seller_skus):
	results = FBAInventory.from_amazon_settings().fetch_for_skus(seller_skus)

	updated = 0
	failed = 0
	for sku_name in seller_skus:
		summary = results.get(sku_name)
		if summary and summary.get("fnSku"):
			frappe.db.set_value("Proxy SKU", sku_name, "fnsku", summary["fnSku"])
			updated += 1
		else:
			failed += 1

	frappe.publish_realtime(
		"msgprint",
		f"FNSKU Fetch Complete: Updated {updated}, Failed {failed}",
	)


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
