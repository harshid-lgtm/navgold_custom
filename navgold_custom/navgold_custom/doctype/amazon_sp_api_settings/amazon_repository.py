# Attribution: Code adapted from eFeone's eSeller Suite (https://github.com/efeone/eseller_suite)
# Original work licensed under the MIT License
# Copyright (c) 2024, eFeone and contributors
#
# Modifications and additions
# Copyright (c) 2025, BWH Studios and contributors
# For license information, please see license.txt

import time

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, getdate

from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import (
	Finances,
	Orders,
	SPAPIError,
)
from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api_settings import (
	AmazonSPAPISettings,
)
from navgold_custom.navgold_custom.utils import format_date_time_to_london


class AmazonRepository:
	def __init__(self, amz_setting: str | AmazonSPAPISettings) -> None:
		if isinstance(amz_setting, str):
			amz_setting = frappe.get_cached_doc("Amazon SP API Settings", amz_setting)

		self.amz_setting = amz_setting
		self.instance_params = dict(
			client_id=self.amz_setting.client_id,
			client_secret=self.amz_setting.get_password("client_secret"),
			refresh_token=self.amz_setting.refresh_token,
			country_code=self.amz_setting.country,
		)

	def return_as_list(self, input) -> list:
		if isinstance(input, list):
			return input
		else:
			return [input]

	def call_sp_api_method(self, sp_api_method, **kwargs) -> dict:
		errors = {}
		max_retries = self.amz_setting.max_retry_limit

		for _x in range(max_retries):
			try:
				result = sp_api_method(**kwargs)
				return result.get("payload")
			except SPAPIError as e:
				if e.error not in errors:
					errors[e.error] = e.error_description

				time.sleep(1)
				continue

		for error in errors:
			msg = f"<b>Error:</b> {error}<br/><b>Error Description:</b> {errors.get(error)}"
			frappe.msgprint(msg, alert=True, indicator="red")
			frappe.log_error(
				message=f"{error}: {errors.get(error)}",
				title=f'Method "{sp_api_method.__name__}" failed',
			)

		self.amz_setting.enable_sync = 0
		self.amz_setting.save()

		frappe.throw(
			_("Scheduled sync has been temporarily disabled because maximum retries have been exceeded!")
		)

	def get_finances_instance(self) -> Finances:
		return Finances(**self.instance_params)

	def get_account(self, name) -> str:
		account_name = frappe.db.get_value("Account", {"account_name": f"Amazon {name}"})

		if not account_name:
			new_account = frappe.new_doc("Account")
			new_account.account_name = f"Amazon {name}"
			new_account.company = self.amz_setting.company
			new_account.parent_account = self.amz_setting.market_place_account_group
			new_account.insert(ignore_permissions=True)
			account_name = new_account.name

		return account_name

	def get_charges_and_fees(self, order_id) -> dict:
		finances = self.get_finances_instance()
		financial_events_payload = self.call_sp_api_method(
			sp_api_method=finances.list_financial_events_by_order_id, order_id=order_id
		)

		charges_and_fees = {
			"charges": [],
			"fees": [],
			"tds": [],
			"service_fees": [],
			"principal_amounts": {},
			"additional_discount": 0,
		}

		if not (financial_events_payload and len(financial_events_payload.get("FinancialEvents", {}))):
			return charges_and_fees

		while True:
			shipment_event_list = financial_events_payload.get("FinancialEvents", {}).get(
				"ShipmentEventList", []
			)
			service_fee_event_list = financial_events_payload.get("FinancialEvents", {}).get(
				"ServiceFeeEventList", []
			)
			next_token = financial_events_payload.get("NextToken")
			principal_amounts = {}
			promotion_discount = 0
			seller_sku = ""
			for shipment_event in shipment_event_list:
				if shipment_event:
					for shipment_item in shipment_event.get("ShipmentItemList", []):
						promotion_list = shipment_item.get("PromotionList", [])
						seller_sku = shipment_item.get("SellerSKU")
						qty = shipment_item.get("QuantityShipped")
						charges = shipment_item.get("ItemChargeList", [])
						fees = shipment_item.get("ItemFeeList", [])
						tds_list = shipment_item.get("ItemTaxWithheldList", [])
						tdss = []
						if tds_list:
							tdss = tds_list[0].get("TaxesWithheld", [])

						for charge in charges:
							charge_type = charge.get("ChargeType")
							amount = charge.get("ChargeAmount", {}).get("CurrencyAmount", 0)

							if charge_type != "Principal" and float(amount) != 0:
								charge_account = self.get_account(charge_type)
								charges_and_fees.get("charges").append(
									{
										"charge_type": "Actual",
										"account_head": charge_account,
										"tax_amount": amount,
										"description": f"{charge_type} for {seller_sku if seller_sku else order_id}",
									}
								)
							if charge_type == "Principal":
								principal_amounts[seller_sku] = round((float(amount) / qty), 2)

						for fee in fees:
							fee_type = fee.get("FeeType")
							amount = fee.get("FeeAmount", {}).get("CurrencyAmount", 0)

							if float(amount) != 0:
								fee_account = self.get_account(fee_type)
								charges_and_fees.get("fees").append(
									{
										"charge_type": "Actual",
										"account_head": fee_account,
										"tax_amount": amount,
										"description": f"{fee_type} for {seller_sku if seller_sku else order_id}",
									}
								)

						for tds in tdss:
							tds_type = tds.get("ChargeType")
							amount = tds.get("ChargeAmount", {}).get("CurrencyAmount", 0)
							if float(amount) != 0:
								tds_account = self.get_account(tds_type)
								charges_and_fees.get("tds").append(
									{
										"charge_type": "Actual",
										"account_head": tds_account,
										"tax_amount": amount,
										"description": f"{tds_type} for {seller_sku if seller_sku else order_id}",
									}
								)

						for promotion in promotion_list:
							amount = promotion.get("PromotionAmount", {}).get("CurrencyAmount", 0)
							promotion_discount += float(amount)

			charges_and_fees["principal_amounts"] = principal_amounts
			charges_and_fees["additional_discount"] = promotion_discount

			for service_fee in service_fee_event_list:
				if service_fee:
					for service_fee_item in service_fee.get("FeeList", []):
						fee_type = service_fee_item.get("FeeType")
						amount = service_fee_item.get("FeeAmount", {}).get("CurrencyAmount", 0)
						if float(amount) != 0:
							fee_account = self.get_account(fee_type)
							charges_and_fees.get("service_fees").append(
								{
									"charge_type": "Actual",
									"account_head": fee_account,
									"tax_amount": amount,
									"description": f"{fee_type} for {seller_sku if seller_sku else order_id}",
								}
							)

			if not next_token:
				break

			financial_events_payload = self.call_sp_api_method(
				sp_api_method=finances.list_financial_events_by_order_id,
				order_id=order_id,
				next_token=next_token,
			)

		return charges_and_fees

	def get_orders_instance(self) -> Orders:
		return Orders(**self.instance_params)

	def create_unmapped_sku(self, order_item, order_id):
		if frappe.db.exists(
			"Amazon Unmapped SKU",
			{
				"amazon_order_id": order_id,
				"proxy_sku": order_item["SellerSKU"],
			},
		):
			return

		frappe.get_doc(
			{
				"doctype": "Amazon Unmapped SKU",
				"proxy_sku": order_item["SellerSKU"],
				"asin": order_item["ASIN"],
				"amazon_order_id": order_id,
			}
		).insert(ignore_permissions=True)

	def get_item_code_and_uom(self, order_item, order_id) -> tuple[str, str] | None:
		try:
			if frappe.db.exists(
				"Proxy SKU",
				{"name": order_item["SellerSKU"], "is_active": 1, "asin": ["!=", None]},
			):
				return frappe.db.get_value(
					"Proxy SKU", order_item["SellerSKU"], ["item", "uom"], as_dict=True
				)

			if frappe.db.exists(
				"Proxy SKU",
				{"name": order_item["SellerSKU"]},
			):
				proxy_sku = frappe.get_cached_doc("Proxy SKU", order_item["SellerSKU"])
				proxy_sku.asin = order_item["ASIN"]
				proxy_sku.is_active = 1
				proxy_sku.save()
				return {"item": proxy_sku.item, "uom": proxy_sku.uom}

		except Exception:
			frappe.log_error(
				title="Amazon Proxy SKU Error",
				message=frappe.get_traceback(),
			)
		self.create_unmapped_sku(order_item, order_id)
		return None

	def get_order_items(self, order_id) -> list:
		orders = self.get_orders_instance()
		order_items_payload = self.call_sp_api_method(sp_api_method=orders.get_order_items, order_id=order_id)

		if not order_items_payload:
			return []

		final_order_items = []
		warehouse = self.amz_setting.warehouse

		while True:
			order_items_list = order_items_payload.get("OrderItems")
			next_token = order_items_payload.get("NextToken")

			for order_item in order_items_list:
				zero_qty_flag = False
				actual_qty = 0
				if order_item.get("QuantityOrdered") >= 0:
					item_amount = float(order_item.get("ItemPrice", {}).get("Amount", 0))
					item_tax = float(order_item.get("ItemTax", {}).get("Amount", 0))
					total_order_value = item_amount + item_tax
					item_qty = float(order_item.get("QuantityOrdered", 0))
					# In case of Cancelled orders Qty will be 0
					if not item_qty:
						item_qty = 1
						zero_qty_flag = True
						actual_qty = order_item.get("ProductInfo").get("NumberOfItems")
					item_rate = item_amount / item_qty
					item_code_and_uom = self.get_item_code_and_uom(order_item, order_id)
					if not item_code_and_uom:
						return []
					final_order_items.append(
						{
							"item_code": item_code_and_uom["item"],
							"item_name": order_item.get("SellerSKU"),
							"description": order_item.get("Title"),
							"rate": item_rate,
							"base_rate": item_rate,
							"qty": item_qty,
							"amount": item_rate * item_qty,
							"base_amount": item_rate * item_qty,
							"uom": item_code_and_uom["uom"],
							"stock_uom": "Nos",
							"warehouse": warehouse,
							"allow_zero_valuation_rate": 1,
							"total_order_value": total_order_value,
							"zero_qty_flag": zero_qty_flag,
							"actual_qty": actual_qty,
						}
					)

			if not next_token:
				break

			order_items_payload = self.call_sp_api_method(
				sp_api_method=orders.get_order_items,
				order_id=order_id,
				next_token=next_token,
			)
		return final_order_items

	def create_customer(self, order) -> str:
		customer = self.get_orders_instance().get_order_buyer_info(order.get("AmazonOrderId"))
		customer = customer.get("payload")
		order_customer_name = customer.get("BuyerName") or customer.get("AmazonOrderId")

		existing_customer_name = frappe.db.get_value(
			"Customer", filters={"name": order_customer_name}, fieldname="name"
		)

		if existing_customer_name:
			filters = [
				["Dynamic Link", "link_doctype", "=", "Customer"],
				["Dynamic Link", "link_name", "=", existing_customer_name],
				["Dynamic Link", "parenttype", "=", "Contact"],
			]

			existing_contacts = frappe.get_list("Contact", filters)

			if not existing_contacts:
				new_contact = frappe.new_doc("Contact")
				new_contact.first_name = order_customer_name
				new_contact.append(
					"links",
					{"link_doctype": "Customer", "link_name": existing_customer_name},
				)
				new_contact.append("email_ids", {"email_id": customer.get("BuyerEmail")})
				new_contact.insert()

			return existing_customer_name
		else:
			new_customer = frappe.new_doc("Customer")
			new_customer.customer_name = order_customer_name
			new_customer.customer_group = self.amz_setting.customer_group
			new_customer.territory = self.amz_setting.territory
			new_customer.customer_type = self.amz_setting.customer_type
			new_customer.save()

			new_contact = frappe.new_doc("Contact")
			new_contact.first_name = order_customer_name
			new_contact.append("links", {"link_doctype": "Customer", "link_name": new_customer.name})

			new_contact.insert()

			return new_customer.name

	def create_address(self, order, customer_name) -> str | None:
		shipping_address = self.get_orders_instance().get_order_address(order.get("AmazonOrderId"))
		shipping_address = shipping_address.get("payload")
		shipping_address = shipping_address.get("ShippingAddress")
		# In case of canceled orders address may not be present
		if not shipping_address:
			return
		existing_address = frappe.db.exists(
			"Address",
			{
				"address_title": shipping_address.get("Name", ""),
				"address_line1": shipping_address.get("AddressLine1", "Not Provided"),
				"address_line2": shipping_address.get("AddressLine2", ""),
				"city": shipping_address.get("City", ""),
				"state": shipping_address.get("StateOrRegion", ""),
				"pincode": shipping_address.get("PostalCode", ""),
				"address_type": "Shipping",
			},
		)
		if existing_address:
			return
		else:
			make_address = frappe.new_doc("Address")
			make_address.address_line1 = shipping_address.get("AddressLine1", "Not Provided")
			make_address.address_line2 = shipping_address.get("AddressLine2", "")
			make_address.city = shipping_address.get("City", "")
			make_address.state = shipping_address.get("StateOrRegion", "")
			make_address.pincode = shipping_address.get("PostalCode", "")
			make_address.address_title = shipping_address.get("Name", "")
			make_address.country = frappe.db.get_value(
				"Country", filters={"code": shipping_address.get("CountryCode", "")}, fieldname="name"
			)

			filters = [
				["Dynamic Link", "link_doctype", "=", "Customer"],
				["Dynamic Link", "link_name", "=", customer_name],
				["Dynamic Link", "parenttype", "=", "Address"],
			]
			existing_address = frappe.get_list("Address", filters)

			for address in existing_address:
				address_doc = frappe.get_doc("Address", address["name"])
				if (
					address_doc.address_line1 == make_address.address_line1
					and address_doc.pincode == make_address.pincode
				):
					return address

			make_address.append("links", {"link_doctype": "Customer", "link_name": customer_name})
			make_address.address_type = "Shipping"
			make_address.insert()

	def create_sales_order(self, order) -> str | None:
		order_id = order.get("AmazonOrderId")
		amazon_order_amount = order.get("OrderTotal", {}).get("Amount", 0)

		# Check if SO already exists and is submitted
		if frappe.db.exists("Sales Order", {"amazon_order_id": order_id}):
			so_id, so_docstatus = frappe.db.get_value(
				"Sales Order", filters={"amazon_order_id": order_id}, fieldname=["name", "docstatus"]
			)
			if so_docstatus:
				return so_id
			so = frappe.get_doc("Sales Order", so_id)
		else:
			so = frappe.new_doc("Sales Order")

		customer_name = self.create_customer(order)
		self.create_address(order, customer_name)

		delivery_date = format_date_time_to_london(order.get("LatestShipDate"))
		transaction_date = format_date_time_to_london(order.get("PurchaseDate"))

		so.amazon_order_id = order_id
		so.marketplace_id = order.get("MarketplaceId")
		so.amazon_order_status = order.get("OrderStatus")
		so.fulfillment_channel = order.get("FulfillmentChannel")
		so.replaced_order_id = order.get("ReplacedOrderId") or ""
		if amazon_order_amount:
			so.amazon_order_amount = amazon_order_amount
		so.amazon_order_status = order.get("OrderStatus")
		so.customer = customer_name
		so.delivery_date = (
			delivery_date if getdate(delivery_date) > getdate(transaction_date) else transaction_date
		)
		so.transaction_date = get_datetime(transaction_date).strftime("%Y-%m-%d")
		so.transaction_time = get_datetime(transaction_date).strftime("%H:%M:%S")
		so.company = self.amz_setting.company
		warehouse = self.amz_setting.warehouse
		if so.fulfillment_channel:
			if so.fulfillment_channel == "AFN":
				warehouse = self.amz_setting.afn_warehouse
		if self.amz_setting.temporary_stock_transfer_required:
			warehouse = self.amz_setting.temporary_order_warehouse
		if order.get("IsBusinessOrder"):
			so.amazon_customer_type = "B2B"
		else:
			so.amazon_customer_type = "B2C"

		# Get order items from the Amazon API
		items = self.get_order_items(order_id)

		if not items:
			return None

		so.items = []
		so.taxes = []
		so.taxes_and_charges = ""
		total_order_value = 0

		# Check if all items are zero-qty
		all_zero_qty = all(item.get("zero_qty_flag", False) for item in items)
		zero_qty_items = []

		for item in items:
			if not all_zero_qty and item.get("zero_qty_flag", True):
				zero_qty_items.append(item)
				continue

			total_order_value += item.get("total_order_value", 0)
			item["warehouse"] = warehouse
			so.append("items", item)

		if len(zero_qty_items) > 0:
			so.cancelled_items = []
			for zero_item in zero_qty_items:
				so.append(
					"cancelled_items",
					{
						"cancelled_item_code": zero_item.get("item_code"),
						"cancelled_item_qty": zero_item.get("actual_qty"),
					},
				)

		if total_order_value:
			so.amazon_order_amount = total_order_value

		taxes_and_charges = self.amz_setting.taxes_charges

		item_lookup = {item["item_code"]: item.get("total_order_value", 0) for item in items}
		for row in so.items:
			total_value = item_lookup.get(row.item_code)
			if total_value:
				row.total_order_value = total_value

		if taxes_and_charges:
			charges_and_fees = self.get_charges_and_fees(order_id)
			if charges_and_fees.get("principal_amounts"):
				principal_amounts = charges_and_fees.get("principal_amounts")
				for item_row in so.items:
					if item_row.item_name and principal_amounts.get(item_row.item_name):
						pricipal_amount = float(principal_amounts.get(item_row.item_name)) or 0
						qty = item_row.qty
						if pricipal_amount:
							item_row.rate = pricipal_amount
							item_row.base_rate = pricipal_amount
							item_row.amount = pricipal_amount * qty
							item_row.base_amount = pricipal_amount * qty

			for charge in charges_and_fees.get("charges"):
				if charge:
					so.append("taxes", charge)

			for fee in charges_and_fees.get("fees"):
				if fee:
					so.append("taxes", fee)

			for tds in charges_and_fees.get("tds"):
				if tds:
					so.append("taxes", tds)

			for service_fee in charges_and_fees.get("service_fees"):
				if service_fee:
					so.append("taxes", service_fee)

			if charges_and_fees.get("additional_discount"):
				so.discount_amount = float(charges_and_fees.get("additional_discount")) * -1

		so.flags.ignore_mandatory = True
		so.disable_rounded_total = 1
		so.run_method("calculate_taxes_and_totals")

		if so.grand_total >= 0:
			try:
				so.save(ignore_permissions=True)
				so.submit()
				frappe.db.commit()
			except Exception as e:
				frappe.log_error(f"Error saving Sales Order for Order {so.amazon_order_id}", e, "Sales Order")
		else:
			if not frappe.db.exists("Amazon Failed Sync Record", {"amazon_order_id": order_id}):
				remarks = (
					f"Failed to create Sales Order for {order_id}. Sales Order grand Total = {so.grand_total}"
				)
				failed_sync_record = frappe.new_doc("Amazon Failed Sync Record")
				failed_sync_record.amazon_order_id = order_id
				failed_sync_record.remarks = remarks
				failed_sync_record.replaced_order_id = so.replaced_order_id
				failed_sync_record.posting_date = so.transaction_date
				failed_sync_record.amazon_order_date = so.transaction_date
				failed_sync_record.grand_total = so.grand_total
				failed_sync_record.amazon_order_amount = so.amazon_order_amount
				if not frappe.db.exists(
					"Amazon Failed Sync Record",
					{"amazon_order_id": order_id, "grand_total": so.grand_total, "remarks": remarks},
				):
					failed_sync_record.save(ignore_permissions=True)

		return so.name

	def get_orders(self, last_updated_after, sync_selected_date_only=0) -> list:
		orders = self.get_orders_instance()
		order_statuses = [
			"Shipped",
			"InvoiceUnconfirmed",
			"Canceled",
			"Unfulfillable",
		]
		fulfillment_channels = ["FBA", "SellerFulfilled"]
		if sync_selected_date_only:
			last_updated_before = add_days(getdate(last_updated_after), 1).strftime("%Y-%m-%d")
			orders_payload = self.call_sp_api_method(
				sp_api_method=orders.get_orders,
				last_updated_after=last_updated_after,
				last_updated_before=last_updated_before,
				order_statuses=order_statuses,
				fulfillment_channels=fulfillment_channels,
				max_results=50,
			)
		else:
			orders_payload = self.call_sp_api_method(
				sp_api_method=orders.get_orders,
				last_updated_after=last_updated_after,
				order_statuses=order_statuses,
				fulfillment_channels=fulfillment_channels,
				max_results=50,
			)

		sales_orders = []

		while True:
			if orders_payload:
				orders_list = orders_payload.get("Orders")
				next_token = orders_payload.get("NextToken")
				if not orders_list or len(orders_list) == 0:
					break
				for order in orders_list:
					sales_order = self.create_sales_order(order)
					if sales_order:
						sales_orders.append(sales_order)
				if not next_token:
					break
				orders_payload = self.call_sp_api_method(
					sp_api_method=orders.get_orders,
					last_updated_after=last_updated_after,
					next_token=next_token,
				)
		return sales_orders

	def get_order(self, amazon_order_ids) -> list:
		orders = self.get_orders_instance()
		order_payload = self.call_sp_api_method(
			sp_api_method=orders.get_order,
			order_id=amazon_order_ids,
		)
		sales_orders = []
		if order_payload:
			try:
				sales_order = self.create_sales_order(order_payload)
				if sales_order:
					sales_orders.append(sales_order)
			except Exception as e:
				frappe.log_error(e)
		return sales_orders


def get_orders(amz_setting_name, last_updated_after, sync_selected_date_only=0) -> list:
	ar = AmazonRepository(amz_setting_name)
	return ar.get_orders(last_updated_after, sync_selected_date_only)


@frappe.whitelist()
def get_order(amz_setting_name, amazon_order_ids) -> list:
	ar = AmazonRepository(amz_setting_name)
	return ar.get_order(amazon_order_ids)
