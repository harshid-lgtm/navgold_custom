# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.background_jobs import is_job_enqueued

BATCH_SIZE = 5000


class NavgoldSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from navgold_custom.navgold_custom.doctype.navgold_published_pricing_item.navgold_published_pricing_item import (
			NavgoldPublishedPricingItem,
		)
		from navgold_custom.navgold_custom.doctype.navgold_zero_stock_item.navgold_zero_stock_item import (
			NavgoldZeroStockItem,
		)
		from navgold_custom.navgold_custom.doctype.strategy_name_mapping.strategy_name_mapping import (
			StrategyNameMapping,
		)

		amazon_pricing_enabled: DF.Check
		amazon_publishing_mode: DF.Literal["Stock Only", "Stock and Prices"]
		auto_sync_cogs: DF.Check
		buying_price_list: DF.Link
		cogs_supplier: DF.Link | None
		commission_threshold: DF.Float
		default_cost_set_template: DF.Link | None
		default_label_owner: DF.Literal["AMAZON", "SELLER", "NONE"]
		default_prep_owner: DF.Literal["AMAZON", "SELLER", "NONE"]
		default_stock_allocation_warehouse: DF.Link | None
		default_strategy_template: DF.Link | None
		enable_pricing_sync: DF.Check
		freeze_amazon_outbound: DF.Check
		pause_strategy_refresh: DF.Check
		fba_inbound_enabled: DF.Check
		fba_source_address: DF.Link | None
		high_price_commission_adjustment: DF.Float
		last_pricing_synced_at: DF.Datetime | None
		min_shelf_expiry_in_days: DF.Int
		multiple_active_item_uom: DF.Check
		price_change_block_direction: DF.Literal["Both", "Drops Only", "Increases Only"]
		price_change_threshold_percent: DF.Float
		pricing_alert_recipients: DF.SmallText | None
		pricing_publishing_mode: DF.Literal["Publish All Prices", "Publish Selected Prices Only"]
		published_pricing_items: DF.Table[NavgoldPublishedPricingItem]
		repricer_api_password: DF.Password | None
		repricer_api_username: DF.Data | None
		repricer_enabled: DF.Check
		send_min_price_to_amazon: DF.Check
		stock_publishing_mode: DF.Literal[
			"Publish Actual Stock",
			"Publish Zero Stock for All Items",
			"Publish Zero Stock for Selected Items",
		]
		strategy_name_mapping: DF.Table[StrategyNameMapping]
		subscribe_and_save_warehouse: DF.Link
		tote_weight_limit: DF.Int
		zero_stock_items: DF.Table[NavgoldZeroStockItem]
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
	def group_strategies_into_pricing(self):
		if not self.strategy_name_mapping:
			frappe.throw(_("Please configure Strategy Name Mapping before grouping."))

		field_map = {
			"FBA Strategy": "fba_strategy",
			"SFP Strategy": "sfp_strategy",
			"Backorder Strategy": "backorder_strategy",
			"Default Strategy": "default_strategy",
		}

		strategy_to_field = {}
		for row in self.strategy_name_mapping:
			pricing_field = field_map.get(row.pricing_field)
			if pricing_field:
				strategy_to_field[row.strategy_name] = pricing_field

		if not strategy_to_field:
			frappe.throw(_("No valid strategy mappings found."))

		total = frappe.db.count("Amazon Product Strategy")
		batch = BATCH_SIZE
		for start in range(0, total, batch):
			frappe.enqueue(
				"navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings.group_strategies_batch",
				strategy_to_field=strategy_to_field,
				start=start,
				batch=batch,
				queue="long",
			)

		frappe.msgprint(
			_("Queued {0} strategies for grouping into pricing records.").format(total),
			alert=True,
		)

	@frappe.whitelist()
	def generate_amazon_pricing_for_all(self):
		return generate_amazon_pricing_for_proxy_skus()

	@frappe.whitelist()
	def submit_active_pricing_now(self):
		queued = []

		if is_job_enqueued("navgold.amazon_manual_pricing_sync"):
			frappe.msgprint(_("A pricing feed submission is already in progress."))
		else:
			frappe.enqueue(
				job_id="navgold.amazon_manual_pricing_sync",
				deduplicate=True,
				method="navgold_custom.navgold_custom.amazon_pricing.amazon_feed_submitter.submit_active_pricing",
				timeout=6000,
				now=frappe.flags.in_test,
			)
			queued.append(_("Amazon pricing feed"))

		if self.repricer_enabled:
			if is_job_enqueued("navgold.repricer_manual_sync"):
				frappe.msgprint(_("A Repricer sync is already in progress."))
			else:
				frappe.enqueue(
					job_id="navgold.repricer_manual_sync",
					deduplicate=True,
					method="navgold_custom.navgold_custom.amazon_pricing.repricer_client.submit_active_repricer",
					timeout=6000,
					now=frappe.flags.in_test,
				)
				queued.append(_("Repricer sync"))

		if queued:
			frappe.msgprint(_("Queued: {0}").format(", ".join(queued)))


def schedule_submit_active_pricing():
	settings = frappe.get_cached_doc("Navgold Settings")
	if not settings.enable_pricing_sync:
		return

	if not settings.amazon_pricing_enabled:
		return

	frappe.enqueue(
		job_id="navgold.amazon_scheduled_pricing_sync",
		deduplicate=True,
		method="navgold_custom.navgold_custom.amazon_pricing.amazon_feed_submitter.submit_active_pricing",
		timeout=6000,
	)


def schedule_submit_active_repricer():
	settings = frappe.get_cached_doc("Navgold Settings")
	if not settings.enable_pricing_sync:
		return

	if not settings.repricer_enabled:
		return

	frappe.enqueue(
		job_id="navgold.repricer_scheduled_sync",
		deduplicate=True,
		method="navgold_custom.navgold_custom.amazon_pricing.repricer_client.submit_active_repricer",
		timeout=6000,
	)


def schedule_bulk_refresh_strategies():
	if frappe.db.get_single_value("Navgold Settings", "pause_strategy_refresh"):
		return

	frappe.enqueue(
		job_id="navgold.scheduled_bulk_refresh_strategies",
		deduplicate=True,
		method="navgold_custom.navgold_custom.amazon_pricing.strategy_selector.bulk_refresh_strategies",
		timeout=6000,
	)


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
	proxy_skus = frappe.get_all(
		"Proxy SKU",
		filters={"is_active": 1},
		fields=["name", "item", "uom"],
		limit_start=start,
		limit_page_length=batch,
	)

	created = 0
	skipped = 0

	for proxy_sku in proxy_skus:
		try:
			if frappe.db.exists("Amazon Product Pricing", {"proxy_sku": proxy_sku.name}):
				skipped += 1
				continue

			pricing = frappe.new_doc("Amazon Product Pricing")
			pricing.proxy_sku = proxy_sku.name
			pricing.is_active = 1
			pricing.insert(ignore_permissions=True)
			created += 1

		except Exception as error:
			frappe.log_error(
				f"Error creating Amazon Product Pricing for {proxy_sku.name}: {error!s}",
				"Bulk Pricing Creation",
			)

	frappe.db.commit()  # nosemgrep: frappe-manual-commit -- background job batch, commit to persist partial progress


def group_strategies_batch(strategy_to_field, start, batch):
	strategies = frappe.get_all(
		"Amazon Product Strategy",
		fields=["name", "proxy_sku", "strategy_name"],
		limit_start=start,
		limit_page_length=batch,
	)

	proxy_sku_strategies = {}
	for strategy in strategies:
		proxy_sku_strategies.setdefault(strategy.proxy_sku, []).append(strategy)

	created = 0
	updated = 0
	skipped = 0

	for proxy_sku, strategy_list in proxy_sku_strategies.items():
		try:
			field_values = {}
			for strategy in strategy_list:
				pricing_field = strategy_to_field.get(strategy.strategy_name)
				if pricing_field:
					field_values[pricing_field] = strategy.name

			if not field_values:
				continue

			existing_pricing = frappe.db.get_value("Amazon Product Pricing", {"proxy_sku": proxy_sku}, "name")

			if existing_pricing:
				pricing = frappe.get_cached_doc("Amazon Product Pricing", existing_pricing)
				has_update = False
				for field, strategy_name in field_values.items():
					if not getattr(pricing, field, None):
						setattr(pricing, field, strategy_name)
						has_update = True

				if has_update:
					pricing.save(ignore_permissions=True)
					updated += 1
				else:
					skipped += 1
			else:
				pricing = frappe.new_doc("Amazon Product Pricing")
				pricing.proxy_sku = proxy_sku
				pricing.is_active = 1
				pricing.update(field_values)
				pricing.insert(ignore_permissions=True)
				created += 1

		except Exception as error:
			frappe.log_error(
				title=f"Error grouping strategies for {proxy_sku} ",
				message=error,
			)

	frappe.db.commit()  # nosemgrep: frappe-manual-commit -- background job batch, commit to persist partial progress
