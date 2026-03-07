# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.utils.xlsxutils import make_xlsx

from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import (
	FBAInventory,
	Listings,
	Reports,
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
		merchant_shipping_group: DF.Data | None
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
		user=frappe.session.user,
	)


@frappe.whitelist()
def bulk_import_listings():
	frappe.enqueue(
		"navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku.process_bulk_listing_import",
		queue="long",
		timeout=1800,
	)
	return {"message": "Listings report import has been queued"}


def process_bulk_listing_import():
	reports_client = Reports.from_amazon_settings()
	tsv_content = reports_client.request_and_download_report("GET_MERCHANT_LISTINGS_ALL_DATA")

	rows = parse_listings_tsv(tsv_content)
	if not rows:
		frappe.publish_realtime("msgprint", "Listings report returned no data", user=frappe.session.user)
		return

	seller_skus = [row["seller-sku"] for row in rows]
	existing_proxy_skus = {
		row.name: row
		for row in frappe.get_all(
			"Proxy SKU",
			filters={"name": ["in", seller_skus]},
			fields=["name", "asin", "is_fba", "is_active", "merchant_shipping_group"],
		)
	}

	existing_asins = set(frappe.get_all("Amazon ASIN", pluck="name"))

	updated = 0
	skipped = 0
	asins_created = 0
	skipped_rows = []
	batch_size = 100
	writes_in_batch = 0

	for row in rows:
		sku_name = row["seller-sku"]
		proxy_sku = existing_proxy_skus.get(sku_name)
		if not proxy_sku:
			skipped += 1
			skipped_rows.append(
				{
					"seller_sku": sku_name,
					"asin": row.get("asin1", "").strip(),
					"status": row.get("status", "").strip(),
					"channel": row.get("fulfilment-channel", "").strip(),
					"reason": "No matching Proxy SKU",
				}
			)
			continue

		asin_value = row.get("asin1", "").strip()
		is_fba = 1 if row.get("fulfilment-channel", "").strip().upper() == "AMAZON_EU" else 0
		is_active = 1 if row.get("status", "").strip().lower() == "active" else 0
		shipping_group = row.get("merchant-shipping-group", "").strip()

		if asin_value and asin_value not in existing_asins:
			asin_doc = frappe.new_doc("Amazon ASIN")
			asin_doc.asin = asin_value
			asin_doc.insert(ignore_permissions=True)
			existing_asins.add(asin_value)
			asins_created += 1
			writes_in_batch += 1

		updates = {}
		if asin_value and proxy_sku.asin != asin_value:
			updates["asin"] = asin_value
		if proxy_sku.is_fba != is_fba:
			updates["is_fba"] = is_fba
		if proxy_sku.is_active != is_active:
			updates["is_active"] = is_active
		if shipping_group and proxy_sku.merchant_shipping_group != shipping_group:
			updates["merchant_shipping_group"] = shipping_group

		if updates:
			frappe.db.set_value("Proxy SKU", sku_name, updates)
			updated += 1
			writes_in_batch += 1

		if writes_in_batch >= batch_size:
			frappe.db.commit()  # nosemgrep: frappe-manual-commit -- background job, commit in batches to avoid MariaDB limits
			writes_in_batch = 0

	if writes_in_batch:
		frappe.db.commit()  # nosemgrep: frappe-manual-commit -- final batch

	skipped_report_url = ""
	if skipped_rows:
		skipped_report_url = save_skipped_report(skipped_rows)

	message = (
		f"Listings Import Complete: Updated {updated}, Skipped {skipped}, ASINs Created {asins_created}."
	)
	if skipped_report_url:
		message += f" <a href='{skipped_report_url}'>Download skipped SKUs report</a>"

	frappe.publish_realtime("msgprint", message, user=frappe.session.user)


def save_skipped_report(skipped_rows):
	data = [["Seller SKU", "ASIN", "Status", "Channel", "Reason"]]
	for row in skipped_rows:
		data.append([row["seller_sku"], row["asin"], row["status"], row["channel"], row["reason"]])

	xlsx_file = make_xlsx(data, "Skipped SKUs")
	file_doc = frappe.new_doc("File")
	file_doc.file_name = "listings_import_skipped_skus.xlsx"
	file_doc.content = xlsx_file.getvalue()
	file_doc.attached_to_doctype = "Navgold Settings"
	file_doc.attached_to_name = "Navgold Settings"
	file_doc.save(ignore_permissions=True)
	return file_doc.file_url


def parse_listings_tsv(tsv_content):
	lines = tsv_content.strip().split("\n")
	if len(lines) < 2:
		return []

	headers = lines[0].split("\t")
	headers = [header.strip().lower() for header in headers]

	rows = []
	for line in lines[1:]:
		values = line.split("\t")
		row = {}
		for index, header in enumerate(headers):
			row[header] = values[index] if index < len(values) else ""
		rows.append(row)

	return rows


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
