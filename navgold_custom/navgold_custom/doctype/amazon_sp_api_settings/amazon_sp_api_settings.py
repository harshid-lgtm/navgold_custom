# Attribution: Code adapted from eFeone's eSeller Suite (https://github.com/efeone/eseller_suite)
# Original work licensed under the MIT License
# Copyright (c) 2024, eFeone and contributors
#
# Modifications and additions
# Copyright (c) 2025, BWH Studios and contributors
# For license information, please see license.txt

from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, get_date_str, get_datetime, now_datetime, today

from navgold_custom.navgold_custom.amazon_repository import (
	get_orders,
)


class AmazonSPAPISettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		adjust_stock_for_returns: DF.Check
		afn_warehouse: DF.Link
		after_date: DF.Date
		client_id: DF.Data
		client_secret: DF.Password
		company: DF.Link
		country: DF.Literal[
			"",
			"CA",
			"US",
			"MX",
			"BR",
			"ES",
			"GB",
			"FR",
			"NL",
			"DE",
			"IT",
			"SE",
			"PL",
			"EG",
			"TR",
			"SA",
			"AE",
			"IN",
			"SG",
			"AU",
			"JP",
		]
		create_item_if_not_exists: DF.Check
		customer_group: DF.Link
		customer_type: DF.Literal["Individual", "Company"]
		enable_sync: DF.Check
		is_active: DF.Check
		is_old_data_migrated: DF.Check
		map_state_data: DF.Check
		market_place_account_group: DF.Link
		max_retry_limit: DF.Int
		merchant_id: DF.Data
		mfn_postage_fee_account_head: DF.Link
		parent_item_group: DF.Link
		price_list: DF.Link
		refresh_token: DF.Text
		return_invoice_stock_adjustment_before: DF.Date | None
		sync_selected_date_only: DF.Check
		taxes_charges: DF.Check
		temporary_order_warehouse: DF.Link | None
		temporary_stock_transfer_required: DF.Check
		territory: DF.Link
		warehouse: DF.Link
	# end: auto-generated types

	def validate(self):
		self.validate_after_date()

		if self.is_active == 0:
			self.enable_sync = 0

		if not self.max_retry_limit:
			self.max_retry_limit = 1
		elif self.max_retry_limit and self.max_retry_limit > 5:
			frappe.throw(frappe._("Value for <b>Max Retry Limit</b> must be less than or equal to 5."))

	def save(self):
		super().save()

	def validate_after_date(self):
		if datetime.strptime(add_days(today(), -60), "%Y-%m-%d") > datetime.strptime(
			get_date_str(self.after_date), "%Y-%m-%d"
		):
			frappe.throw(_("The date must be within the last 60 days."))

	@frappe.whitelist()
	def get_order_details(self):
		if not self.is_active:
			frappe.msgprint(_("Please enable the Amazon SP API Settings {0}.").format(frappe.bold(self.name)))
			return

		if frappe.conf.developer_mode:
			get_orders(last_updated_after=self.after_date)
			return

		job_name = "Get Amazon Orders"

		if frappe.db.get_all("RQ Job", {"job_name": job_name, "status": ["in", ["queued", "started"]]}):
			frappe.msgprint(_("The order details are currently being fetched in the background."))
			return

		frappe.enqueue(
			job_name=job_name,
			method=get_orders,
			last_updated_after=self.after_date,
			sync_selected_date_only=self.sync_selected_date_only,
			timeout=6000,
			now=frappe.flags.in_test,
		)

		frappe.msgprint(_("Order details will be fetched in the background."))

	def get_marketplace_id(self) -> str:
		from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import MARKETPLACES

		for region_data in MARKETPLACES.values():
			if self.country in region_data:
				return region_data[self.country]
		return ""


def schedule_get_order_details():
	amz_setting = frappe.get_single("Amazon SP API Settings")
	if not amz_setting.enable_sync:
		return

	job_name = "Scheduled Amazon Order Sync"
	if frappe.db.get_all("RQ Job", {"job_name": job_name, "status": ["in", ["queued", "started"]]}):
		return

	if amz_setting.last_synced_at:
		fetch_after = get_datetime(amz_setting.last_synced_at) - timedelta(minutes=10)
	else:
		fetch_after = today()

	frappe.enqueue(
		job_name=job_name,
		method=get_orders,
		last_updated_after=fetch_after,
		timeout=6000,
	)
