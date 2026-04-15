import time
from datetime import datetime, timedelta

import frappe
from frappe.utils import add_days, convert_utc_to_system_timezone, flt, get_datetime, getdate, now_datetime

from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import (
	Orders,
	SPAPIError,
)


def parse_amazon_utc(utc_time_str):
	utc_time = datetime.strptime(utc_time_str, "%Y-%m-%dT%H:%M:%SZ")
	local_time = convert_utc_to_system_timezone(utc_time)
	return local_time.strftime("%Y-%m-%d %H:%M:%S")


class AmazonRepository:
	def __init__(self):
		self.amz_setting = frappe.get_single("Amazon SP API Settings")
		self.instance_params = {
			"client_id": self.amz_setting.client_id,
			"client_secret": self.amz_setting.get_password("client_secret"),
			"refresh_token": self.amz_setting.refresh_token,
			"country_code": self.amz_setting.country,
		}

	def call_sp_api_method(self, sp_api_method, **kwargs):
		max_retries = self.amz_setting.max_retry_limit
		errors = {}

		for _ in range(max_retries):
			try:
				result = sp_api_method(**kwargs)
				return result.get("payload")
			except SPAPIError as error:
				if error.error not in errors:
					errors[error.error] = error.error_description
				time.sleep(1)

		for error_code, description in errors.items():
			frappe.log_error(
				title=f'SP-API method "{sp_api_method.__name__}" failed',
				message=f"{error_code}: {description}",
			)

	def get_orders_instance(self):
		return Orders(**self.instance_params)

	def resolve_proxy_sku(self, order_item, order_id):
		seller_sku = order_item.get("SellerSKU")
		asin = order_item.get("ASIN")

		try:
			if frappe.db.exists("Proxy SKU", {"name": seller_sku, "is_active": 1, "asin": ["!=", None]}):
				return frappe.db.get_value("Proxy SKU", seller_sku, ["item", "uom"], as_dict=True)

			if frappe.db.exists("Proxy SKU", {"name": seller_sku}):
				proxy_sku = frappe.get_cached_doc("Proxy SKU", seller_sku)
				proxy_sku.asin = asin
				proxy_sku.is_active = 1
				proxy_sku.save()
				return {"item": proxy_sku.item, "uom": proxy_sku.uom}
		except Exception:
			frappe.log_error(
				title=f"Proxy SKU lookup failed for {seller_sku}",
				message=frappe.get_traceback(),
			)

		return None

	def create_unmapped_sku(self, order_item, order_id):
		seller_sku = order_item.get("SellerSKU")
		if frappe.db.exists("Amazon Unmapped SKU", {"proxy_sku": seller_sku, "amazon_order_id": order_id}):
			return

		doc = frappe.get_doc(
			{
				"doctype": "Amazon Unmapped SKU",
				"proxy_sku": seller_sku,
				"asin": order_item.get("ASIN"),
				"amazon_order_id": order_id,
			}
		)
		doc.insert(ignore_permissions=True)

		self.notify_unmapped_sku(doc)

	@staticmethod
	def notify_unmapped_sku(unmapped_doc):
		notification = frappe.new_doc("Notification Log")
		notification.subject = f"Unmapped SKU Alert: Amazon Order {unmapped_doc.amazon_order_id}"
		notification.type = "Alert"
		notification.email_content = (
			f"Unmapped SKU detected for Amazon Order {unmapped_doc.amazon_order_id}.<br>"
			f"<b>Proxy SKU:</b> {unmapped_doc.proxy_sku}<br>"
			f"<b>ASIN:</b> {unmapped_doc.asin or 'N/A'}<br><br>"
			f"This sales order could not be fetched because the proxy SKU is not mapped to an item.<br>"
			f"To resolve this, please map the proxy SKU in the Proxy SKU master."
		)
		notification.document_type = "Amazon Unmapped SKU"
		notification.document_name = unmapped_doc.name
		notification.from_user = "Administrator"
		notification.insert(ignore_permissions=True)

	def get_order_items(self, order_id):
		orders = self.get_orders_instance()
		payload = self.call_sp_api_method(sp_api_method=orders.get_order_items, order_id=order_id)

		if not payload:
			return []

		raw_items = []
		unmapped_items = []
		warehouse = self.amz_setting.warehouse

		while True:
			order_items_list = payload.get("OrderItems", [])
			next_token = payload.get("NextToken")

			for order_item in order_items_list:
				item_qty = flt(order_item.get("QuantityOrdered", 0))
				item_amount = flt(order_item.get("ItemPrice", {}).get("Amount", 0))

				zero_qty_flag = False
				actual_qty = 0
				if not item_qty:
					item_qty = 1
					zero_qty_flag = True
					actual_qty = (order_item.get("ProductInfo") or {}).get("NumberOfItems", 0)

				item_rate = item_amount / item_qty if item_qty else 0

				resolved = self.resolve_proxy_sku(order_item, order_id)
				if not resolved:
					unmapped_items.append(order_item)
					continue

				raw_items.append(
					{
						"item_code": resolved["item"],
						"item_name": order_item.get("SellerSKU"),
						"description": order_item.get("Title"),
						"rate": item_rate,
						"qty": item_qty,
						"uom": resolved["uom"],
						"stock_uom": "Nos",
						"warehouse": warehouse,
						"allow_zero_valuation_rate": 1,
						"zero_qty_flag": zero_qty_flag,
						"actual_qty": actual_qty,
					}
				)

			if not next_token:
				break

			payload = self.call_sp_api_method(
				sp_api_method=orders.get_order_items,
				order_id=order_id,
				next_token=next_token,
			)

		if unmapped_items:
			for unmapped_item in unmapped_items:
				self.create_unmapped_sku(unmapped_item, order_id)

			skus = [item.get("SellerSKU") for item in unmapped_items]
			self.notify_unmapped_skus(order_id, skus)
			raise Exception(f"Order skipped. Unmapped SKUs: {', '.join(skus)}")

		return raw_items

	@staticmethod
	def notify_unmapped_skus(order_id, skus):
		notification = frappe.new_doc("Notification Log")
		notification.subject = f"Unmapped SKUs Alert: Order {order_id} skipped"
		notification.type = "Alert"
		notification.email_content = (
			f"Order {order_id} was skipped due to unmapped SKUs.<br>"
			f"<b>Unmapped SKUs:</b> {', '.join(skus)}<br><br>"
			f"Please map these SKUs in the Proxy SKU master to process these orders."
		)
		notification.from_user = "Administrator"
		notification.insert(ignore_permissions=True)

	def create_customer(self, order):
		buyer_info = self.get_orders_instance().get_order_buyer_info(order.get("AmazonOrderId"))
		buyer_info = buyer_info.get("payload", {})
		customer_name = buyer_info.get("BuyerName") or order.get("AmazonOrderId")

		existing_customer = frappe.db.get_value("Customer", {"name": customer_name}, "name")

		if existing_customer:
			return existing_customer

		new_customer = frappe.new_doc("Customer")
		new_customer.customer_name = customer_name
		new_customer.customer_group = self.amz_setting.customer_group
		new_customer.territory = self.amz_setting.territory
		new_customer.customer_type = self.amz_setting.customer_type
		new_customer.save()

		contact = frappe.new_doc("Contact")
		contact.first_name = customer_name
		contact.append("links", {"link_doctype": "Customer", "link_name": new_customer.name})
		contact.insert()

		return new_customer.name

	def create_address(self, order, customer_name):
		address_payload = self.get_orders_instance().get_order_address(order.get("AmazonOrderId"))
		shipping_address = (address_payload.get("payload") or {}).get("ShippingAddress")

		if not shipping_address:
			return

		address_line1 = shipping_address.get("AddressLine1", "Not Provided")
		pincode = shipping_address.get("PostalCode", "")

		existing = frappe.db.exists(
			"Address",
			{
				"address_title": shipping_address.get("Name", ""),
				"address_line1": address_line1,
				"pincode": pincode,
				"address_type": "Shipping",
			},
		)
		if existing:
			return

		country = frappe.db.get_value("Country", {"code": shipping_address.get("CountryCode", "")}, "name")

		new_address = frappe.new_doc("Address")
		new_address.address_title = shipping_address.get("Name", "")
		new_address.address_line1 = address_line1
		new_address.address_line2 = shipping_address.get("AddressLine2", "")
		new_address.city = shipping_address.get("City", "")
		new_address.state = shipping_address.get("StateOrRegion", "")
		new_address.pincode = pincode
		new_address.country = country
		new_address.address_type = "Shipping"
		new_address.append("links", {"link_doctype": "Customer", "link_name": customer_name})
		new_address.insert()

	def create_sales_order(self, order):
		order_id = order.get("AmazonOrderId")
		amazon_order_amount = order.get("OrderTotal", {}).get("Amount", 0)

		existing_so = frappe.db.get_value(
			"Sales Order",
			{"amazon_order_id": order_id, "docstatus": ["!=", 2]},
			["name", "docstatus"],
			as_dict=True,
		)

		if existing_so and existing_so.docstatus == 1:
			return existing_so.name

		try:
			items = self.get_order_items(order_id)
			if not items:
				return None

			if existing_so:
				sales_order = frappe.get_cached_doc("Sales Order", existing_so.name)
			else:
				sales_order = frappe.new_doc("Sales Order")

			customer_name = self.create_customer(order)
			self.create_address(order, customer_name)

			delivery_date = getdate(parse_amazon_utc(order.get("LatestShipDate")))
			transaction_date = getdate(parse_amazon_utc(order.get("PurchaseDate")))

			warehouse = self.amz_setting.warehouse
			if order.get("FulfillmentChannel") == "AFN":
				warehouse = self.amz_setting.afn_warehouse

			sales_order.update(
				{
					"amazon_order_id": order_id,
					"marketplace_id": order.get("MarketplaceId"),
					"amazon_order_status": order.get("OrderStatus"),
					"fulfillment_channel": order.get("FulfillmentChannel"),
					"amazon_customer_type": "B2B" if order.get("IsBusinessOrder") else "B2C",
					"customer": customer_name,
					"company": self.amz_setting.company,
					"transaction_date": transaction_date,
					"delivery_date": delivery_date if delivery_date > transaction_date else transaction_date,
				}
			)

			if amazon_order_amount:
				sales_order.amazon_order_amount = amazon_order_amount

			sales_order.items = []
			all_zero_qty = all(item.get("zero_qty_flag", False) for item in items)

			for item in items:
				if not all_zero_qty and item.get("zero_qty_flag", True):
					continue
				item["warehouse"] = warehouse
				sales_order.append("items", item)

			sales_order.set_taxes()
			for tax_row in sales_order.taxes:
				tax_row.included_in_print_rate = 1

			sales_order.flags.ignore_mandatory = True
			sales_order.flags.ignore_pricing_rule = True
			sales_order.disable_rounded_total = 1
			sales_order.run_method("calculate_taxes_and_totals")

			sales_order.save(ignore_permissions=True)
			sales_order.submit()
			return sales_order.name

		except Exception as error:
			self.create_failed_sync_record(order_id, str(error), order=order)
			return None

	def create_failed_sync_record(self, order_id, remarks, order=None):
		if frappe.db.exists("Amazon Failed Sync Record", {"amazon_order_id": order_id}):
			return

		record = frappe.new_doc("Amazon Failed Sync Record")
		record.amazon_order_id = order_id
		record.remarks = remarks
		record.posting_date = getdate()

		if order:
			record.amazon_order_amount = flt(order.get("OrderTotal", {}).get("Amount", 0))
			record.payload = frappe.as_json(order)

			purchase_date = order.get("PurchaseDate")
			if purchase_date:
				record.amazon_order_date = getdate(parse_amazon_utc(purchase_date))

		record.insert(ignore_permissions=True)

	def get_orders(self, last_updated_after, sync_selected_date_only=0):
		orders = self.get_orders_instance()
		order_statuses = ["Shipped", "InvoiceUnconfirmed", "Canceled", "Unfulfillable"]
		fulfillment_channels = ["FBA", "SellerFulfilled"]

		kwargs = {
			"last_updated_after": last_updated_after,
			"order_statuses": order_statuses,
			"fulfillment_channels": fulfillment_channels,
			"max_results": 50,
		}
		if sync_selected_date_only:
			end_of_day = get_datetime(add_days(getdate(last_updated_after), 1))
			two_minutes_ago = now_datetime() - timedelta(minutes=3)
			last_updated_before = min(end_of_day, two_minutes_ago)
			kwargs["last_updated_before"] = last_updated_before.strftime("%Y-%m-%dT%H:%M:%SZ")

		orders_payload = self.call_sp_api_method(sp_api_method=orders.get_orders, **kwargs)

		sales_orders = []
		order_count = 0

		while True:
			if not orders_payload:
				break

			orders_list = orders_payload.get("Orders", [])
			next_token = orders_payload.get("NextToken")

			if not orders_list:
				break

			for order in orders_list:
				try:
					sales_order = self.create_sales_order(order)
					if sales_order:
						sales_orders.append(sales_order)
					order_count += 1

					if order_count % 10 == 0:
						frappe.db.commit()  # nosemgrep: manual-commit, batch commit every 10 orders

				except Exception:
					order_id = (order or {}).get("AmazonOrderId", "Unknown")
					self.create_failed_sync_record(order_id, frappe.get_traceback(), order=order)

			frappe.db.commit()  # nosemgrep: manual-commit, commit remaining orders

			if not next_token:
				break

			orders_payload = self.call_sp_api_method(
				sp_api_method=orders.get_orders,
				last_updated_after=last_updated_after,
				next_token=next_token,
			)

		return sales_orders

	def get_order(self, amazon_order_ids):
		orders = self.get_orders_instance()
		order_payload = self.call_sp_api_method(
			sp_api_method=orders.get_order,
			order_id=amazon_order_ids,
		)

		sales_orders = []
		if order_payload:
			sales_order = self.create_sales_order(order_payload)
			if sales_order:
				sales_orders.append(sales_order)

		return sales_orders


@frappe.whitelist()
def get_orders(last_updated_after: str, sync_selected_date_only: int = 0):
	repository = AmazonRepository()
	result = repository.get_orders(last_updated_after, sync_selected_date_only)

	frappe.db.set_single_value("Amazon SP API Settings", "last_synced_at", frappe.utils.now_datetime())

	return result


@frappe.whitelist()
def get_order(amazon_order_ids: str):
	repository = AmazonRepository()
	return repository.get_order(amazon_order_ids)
