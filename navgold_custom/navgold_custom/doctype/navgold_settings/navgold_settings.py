# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

BATCH_SIZE = 5000


class NavgoldSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		amazon_pricing_enabled: DF.Check
		auto_sync_cogs: DF.Check
		buying_price_list: DF.Link
		cogs_supplier: DF.Link | None
		commission_threshold: DF.Float
		default_cost_set_template: DF.Link | None
		default_label_owner: DF.Literal["AMAZON", "SELLER", "NONE"]
		default_prep_owner: DF.Literal["AMAZON", "SELLER", "NONE"]
		default_stock_allocation_warehouse: DF.Link | None
		default_strategy_template: DF.Link | None
		fba_inbound_enabled: DF.Check
		fba_source_address: DF.Link | None
		high_price_commission_adjustment: DF.Float
		min_shelf_expiry_in_days: DF.Int
		multiple_active_item_uom: DF.Check
		repricer_api_password: DF.Password | None
		repricer_api_username: DF.Data | None
		repricer_enabled: DF.Check
		subscribe_and_save_warehouse: DF.Link
		tote_weight_limit: DF.Int
	# end: auto-generated types

	def get_source_address(self, state_key="stateOrRegion"):
		if not self.fba_source_address:
			frappe.throw(_("Source address is not configured in Navgold Settings."))

		address_doc = frappe.get_cached_doc("Address", self.fba_source_address)
		country_code = frappe.db.get_value("Country", address_doc.country, "code") or address_doc.country

		address = {
			"name": address_doc.address_title or "",
			"addressLine1": address_doc.address_line1,
			"city": address_doc.city or "",
			"countryCode": (country_code or "").upper(),
			"postalCode": address_doc.pincode or "",
		}

		if address_doc.address_line2:
			address["addressLine2"] = address_doc.address_line2
		if address_doc.phone:
			address["phoneNumber"] = address_doc.phone
		if address_doc.state:
			address[state_key] = address_doc.state
		if address_doc.email_id:
			address["email"] = address_doc.email_id

		required = ["name", "addressLine1", "city", "postalCode", "countryCode"]
		if any(not address.get(key) for key in required):
			frappe.throw(_("Source address is incomplete in Navgold Settings."))

		return address

	@frappe.whitelist()
	def generate_proxy_skus_for_items(self):
		total = frappe.db.count("UOM Conversion Detail", filters={"parenttype": "Item"})

		batch = BATCH_SIZE
		for start in range(0, total, batch):
			frappe.enqueue(
				"navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings.generate_proxy_skus_batch",
				start=start,
				batch=batch,
				queue="long",
			)

	@frappe.whitelist()
	def generate_amazon_pricing_for_all(self):
		"""
		Generate Amazon Product Pricing for all active Proxy SKUs that don't have one.
		"""
		return generate_amazon_pricing_for_proxy_skus()


def generate_proxy_skus_batch(start, batch):
	uoms = frappe.get_all(
		"UOM Conversion Detail",
		filters={"parenttype": "Item"},
		fields=["parent", "uom"],
		limit_start=start,
		limit_page_length=batch,
	)
	for uom in uoms:
		try:
			if not frappe.db.exists("Proxy SKU", {"item": uom.parent, "uom": uom.uom}):
				doc = frappe.new_doc("Proxy SKU")
				doc.item = uom.parent
				doc.uom = uom.uom
				doc.insert(ignore_permissions=True)
		except Exception as e:
			frappe.log_error(f"Error creating Proxy SKU for {uom.parent} - {uom.uom}", str(e))


@frappe.whitelist()
def generate_amazon_pricing_for_proxy_skus():
	"""
	Bulk create Amazon Product Pricing records for all Proxy SKUs that don't have one.
	Runs in batches via background jobs.
	"""
	# Get count of Proxy SKUs without pricing
	total = frappe.db.count("Proxy SKU", filters={"is_active": 1})

	batch = BATCH_SIZE
	for start in range(0, total, batch):
		frappe.enqueue(
			"navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings.generate_pricing_batch",
			start=start,
			batch=batch,
			queue="long",
		)

	return {"message": f"Queued {total} Proxy SKUs for pricing creation"}


def generate_pricing_batch(start, batch):
	"""
	Process a batch of Proxy SKUs and create Amazon Product Pricing records.
	"""
	proxy_skus = frappe.get_all(
		"Proxy SKU",
		filters={"is_active": 1},
		fields=["name", "item", "uom"],
		limit_start=start,
		limit_page_length=batch,
	)

	created = 0
	skipped = 0

	for proxy in proxy_skus:
		try:
			# Check if pricing already exists
			if frappe.db.exists("Amazon Product Pricing", {"proxy_sku": proxy.name}):
				skipped += 1
				continue

			# Create new pricing record
			pricing = frappe.new_doc("Amazon Product Pricing")
			pricing.proxy_sku = proxy.name
			pricing.is_active = 1
			pricing.insert(ignore_permissions=True)
			created += 1

		except Exception as e:
			frappe.log_error(
				f"Error creating Amazon Product Pricing for {proxy.name}: {e!s}", "Bulk Pricing Creation"
			)

	frappe.db.commit()  # nosemgrep: frappe-manual-commit -- background job batch, commit to persist partial progress
	frappe.logger().info(f"Bulk Pricing: Created {created}, Skipped {skipped}")
