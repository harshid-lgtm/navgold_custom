# Attribution: Code adapted from eFeone's eSeller Suite (https://github.com/efeone/eseller_suite)
# Original work licensed under the MIT License
# Copyright (c) 2024, eFeone and contributors
#
# Modifications and additions
# Copyright (c) 2025, BWH Studios and contributors
# For license information, please see license.txt

from datetime import datetime, timedelta

import frappe
import pytz
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, get_date_str, getdate, now_datetime, today


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
		from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_repository import (
			get_orders,
		)

		if not self.is_active:
			frappe.msgprint(_("Please enable the Amazon SP API Settings {0}.").format(frappe.bold(self.name)))
			return

		if frappe.conf.developer_mode:
			get_orders(amz_setting_name=self.name, last_updated_after=self.after_date)
			return

		job_name = f"Get Amazon Orders - {self.name}"

		if frappe.db.get_all("RQ Job", {"job_name": job_name, "status": ["in", ["queued", "started"]]}):
			frappe.msgprint(_("The order details are currently being fetched in the background."))
			return

		frappe.enqueue(
			job_name=job_name,
			method=get_orders,
			amz_setting_name=self.name,
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


# Called via a hook in every hour.
def schedule_get_order_details():
	current_datetime = now_datetime()

	yesterday_23 = (current_datetime - timedelta(days=1)).replace(hour=23, minute=0, second=0, microsecond=0)
	today_01 = current_datetime.replace(hour=1, minute=0, second=0, microsecond=0)

	if (
		yesterday_23 <= current_datetime < today_01
	):  # Makes it so that the hourly scheduler won't work at midnight and the daily one will
		return

	from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_repository import (
		get_orders,
	)

	system_timezone = frappe.db.get_single_value("System Settings", "time_zone")

	local_tz = pytz.timezone(system_timezone)
	gmt_tz = pytz.timezone("GMT")

	local_datetime = local_tz.localize(current_datetime)
	gmt_datetime = local_datetime.astimezone(gmt_tz)
	current_date = gmt_datetime.strftime("%Y-%m-%d")

	amz_settings = frappe.get_all(
		"Amazon SP API Settings",
		filters={"is_active": 1, "enable_sync": 1},
		fields=["name"],
	)

	for amz_setting in amz_settings:
		get_orders(amz_setting_name=amz_setting.name, last_updated_after=current_date)


# Called via a hook every day to sync data of the previous day.
def schedule_get_order_details_daily():
	from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_repository import (
		get_orders,
	)

	from_date = add_days(getdate(), -1).strftime("%Y-%m-%d")

	amz_settings = frappe.get_all(
		"Amazon SP API Settings",
		filters={"is_active": 1, "enable_sync": 1},
		fields=["name"],
	)

	for amz_setting in amz_settings:
		get_orders(amz_setting_name=amz_setting.name, last_updated_after=from_date)
