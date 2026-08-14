import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import (
	add_days,
	add_to_date,
	cint,
	convert_utc_to_system_timezone,
	flt,
	get_datetime,
	get_system_timezone,
	getdate,
	now_datetime,
)
from frappe.utils.synchronization import filelock

from navgold_custom.navgold_custom.amazon_pricing.alerts import (
	alert_orders_canceled,
	alert_status_refresh_gap,
	alert_status_refresh_truncated,
)
from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import (
	Orders,
	SPAPIError,
)

STATUS_REFRESH_OVERLAP_MINUTES = 15
STATUS_REFRESH_WINDOW_HOURS = 6
STATUS_REFRESH_MAX_BACKLOG_DAYS = 7
STATUS_REFRESH_MAX_PAGES = 10
STATUS_REFRESH_LAG_MINUTES = 3

ORDER_SYNC_OVERLAP_MINUTES = 15
ORDER_SYNC_LAG_MINUTES = 3
ORDER_SYNC_MAX_RECOVERED_ORDERS = 20

AMAZON_ORDER_STATUSES = (
	"PendingAvailability",
	"Pending",
	"Unshipped",
	"PartiallyShipped",
	"Shipped",
	"InvoiceUnconfirmed",
	"Canceled",
	"Unfulfillable",
)

UNAUTHORISED_ORDER_STATUSES = ("Pending", "PendingAvailability")

CLOSED_ORDER_STATUSES = ("Canceled", "Unfulfillable")

PICKABLE_ORDER_STATUSES = ("Unshipped", "PartiallyShipped")


def parse_amazon_utc(utc_time_str):
	utc_time = datetime.strptime(utc_time_str, "%Y-%m-%dT%H:%M:%SZ")
	local_time = convert_utc_to_system_timezone(utc_time)
	return local_time.strftime("%Y-%m-%d %H:%M:%S")


def format_local_as_amazon_utc(local_datetime):
	system_datetime = get_datetime(local_datetime).replace(tzinfo=ZoneInfo(get_system_timezone()))
	return system_datetime.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


class AmazonRepository:
	def __init__(self):
		self.refresh_truncated = False
		self.sync_truncated = False
		self.recovered_order_count = 0
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

		if payload is None:
			raise Exception(f"Could not fetch order items for {order_id} (SP-API call failed or throttled)")

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

			if payload is None:
				raise Exception(
					f"Could not fetch all order items for {order_id} (SP-API call failed or throttled)"
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

		# a cancelled order is not demand: "Canceled" is absent from UNAUTHORISED_ORDER_STATUSES, so
		# without this the order sync submits it, reserving stock and raising a purchase request
		# nothing will ever settle
		if order.get("OrderStatus") in CLOSED_ORDER_STATUSES:
			return None

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
				self.create_failed_sync_record(order_id, "No order items returned by Amazon", order=order)
				return None

			if existing_so:
				sales_order = frappe.get_doc("Sales Order", existing_so.name)
			else:
				sales_order = frappe.new_doc("Sales Order")

			customer_name = self.create_customer(order)
			self.create_address(order, customer_name)

			if sales_order.customer and sales_order.customer != customer_name:
				sales_order.customer_name = None
				sales_order.customer_address = None
				sales_order.address_display = None

			purchase_date_str = order.get("PurchaseDate")
			latest_ship_date_str = order.get("LatestShipDate")
			latest_delivery_date_str = order.get("LatestDeliveryDate")

			transaction_date = (
				getdate(parse_amazon_utc(purchase_date_str)) if purchase_date_str else getdate()
			)
			dispatch_date = getdate(parse_amazon_utc(latest_ship_date_str)) if latest_ship_date_str else None
			delivery_date = (
				getdate(parse_amazon_utc(latest_delivery_date_str)) if latest_delivery_date_str else None
			)

			if not delivery_date:
				delivery_date = dispatch_date or transaction_date

			if delivery_date < transaction_date:
				delivery_date = transaction_date

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
					"is_prime": 1 if order.get("IsPrime") else 0,
					"customer": customer_name,
					"company": self.amz_setting.company,
					"transaction_date": transaction_date,
					"delivery_date": delivery_date,
					"dispatch_date": dispatch_date,
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

			try:
				from erpnext.accounts.services.taxes import TaxService

				TaxService(sales_order).set_taxes()
			except ImportError:
				sales_order.set_taxes()
			for tax_row in sales_order.taxes:
				tax_row.included_in_print_rate = 1

			sales_order.flags.ignore_mandatory = True
			sales_order.flags.ignore_pricing_rule = True
			sales_order.disable_rounded_total = 1
			sales_order.run_method("calculate_taxes_and_totals")

			sales_order.save(ignore_permissions=True)

			if order.get("OrderStatus") not in UNAUTHORISED_ORDER_STATUSES:
				sales_order.submit()

			return sales_order.name

		except Exception as error:
			self.create_failed_sync_record(order_id, str(error), order=order)
			return None

	def create_failed_sync_record(self, order_id, remarks, order=None):
		if frappe.db.exists("Amazon Failed Sync Record", {"amazon_order_id": order_id, "status": "Failed"}):
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

	def get_orders(self, created_after, sync_selected_date_only=0):
		orders = self.get_orders_instance()
		order_statuses = list(AMAZON_ORDER_STATUSES)
		fulfillment_channels = ["FBA", "SellerFulfilled"]

		kwargs = {
			"created_after": format_local_as_amazon_utc(created_after),
			"order_statuses": order_statuses,
			"fulfillment_channels": fulfillment_channels,
			"max_results": 50,
		}
		if sync_selected_date_only:
			selected_date = getdate(created_after)
			kwargs["created_after"] = format_local_as_amazon_utc(get_datetime(selected_date))
			end_of_day = get_datetime(add_days(selected_date, 1))
			indexed_up_to = now_datetime() - timedelta(minutes=ORDER_SYNC_LAG_MINUTES)
			created_before = min(end_of_day, indexed_up_to)
			kwargs["created_before"] = format_local_as_amazon_utc(created_before)

		orders_payload = self.call_sp_api_method(sp_api_method=orders.get_orders, **kwargs)

		if orders_payload is None:
			return None

		sales_orders = []
		order_count = 0

		while orders_payload:
			orders_list = orders_payload.get("Orders", [])

			existing_orders = self.get_sales_orders_by_amazon_id(
				[order.get("AmazonOrderId") for order in orders_list]
			)

			for order in orders_list:
				try:
					existing_so = existing_orders.get(order.get("AmazonOrderId"))
					if existing_so:
						sales_order = self.update_order_status(order, existing_so)
					else:
						sales_order = self.create_sales_order(order)

					if sales_order:
						sales_orders.append(sales_order)
					order_count += 1

					if order_count % 10 == 0:
						# nosemgrep: frappe-manual-commit -- background job batch, commit every 10 orders
						frappe.db.commit()

				except Exception:
					order_id = (order or {}).get("AmazonOrderId", "Unknown")
					self.create_failed_sync_record(order_id, frappe.get_traceback(), order=order)

			frappe.db.commit()  # nosemgrep

			next_token = orders_payload.get("NextToken")
			if not next_token:
				break

			orders_payload = self.call_sp_api_method(
				sp_api_method=orders.get_orders,
				created_after=kwargs["created_after"],
				next_token=next_token,
			)

			if not orders_payload:
				self.sync_truncated = True
				break

		return sales_orders

	@staticmethod
	def get_sales_orders_by_amazon_id(order_ids):
		if not order_ids:
			return {}

		rows = frappe.get_all(
			"Sales Order",
			filters={"amazon_order_id": ["in", order_ids], "docstatus": ["!=", 2]},
			fields=["name", "docstatus", "status", "amazon_order_status", "amazon_order_id"],
		)
		return {row.amazon_order_id: row for row in rows}

	def update_order_status(self, order, existing_so):
		amazon_status = order.get("OrderStatus")

		if not existing_so:
			return None

		if existing_so.amazon_order_status == amazon_status and not self.is_closed_but_open(
			existing_so, amazon_status
		):
			return None

		with filelock(f"amazon_order_{order.get('AmazonOrderId')}", timeout=60):
			if amazon_status in CLOSED_ORDER_STATUSES:
				frappe.db.set_value("Sales Order", existing_so.name, "amazon_order_status", amazon_status)
				if existing_so.docstatus == 1:
					frappe.get_doc("Sales Order", existing_so.name).update_status("Closed")
				return existing_so.name

			if existing_so.docstatus == 0:
				return self.create_sales_order(order)

			frappe.db.set_value("Sales Order", existing_so.name, "amazon_order_status", amazon_status)
			return existing_so.name

	@staticmethod
	def is_closed_but_open(existing_so, amazon_status):
		# the status field is written before the order is closed, so a failed close would otherwise
		# short-circuit on the next refresh and strand the order open forever
		return (
			amazon_status in CLOSED_ORDER_STATUSES
			and existing_so.docstatus == 1
			and existing_so.status != "Closed"
		)

	def sync_order(self, order, existing_so):
		if existing_so:
			return self.update_order_status(order, existing_so)

		if order.get("OrderStatus") in CLOSED_ORDER_STATUSES:
			return None

		order_id = order.get("AmazonOrderId")

		if self.recovered_order_count >= ORDER_SYNC_MAX_RECOVERED_ORDERS:
			self.refresh_truncated = True
			return None

		if frappe.db.exists("Amazon Failed Sync Record", {"amazon_order_id": order_id, "status": "Failed"}):
			return None

		with filelock(f"amazon_order_{order_id}", timeout=60):
			if frappe.db.exists("Sales Order", {"amazon_order_id": order_id, "docstatus": 2}):
				return None

			self.recovered_order_count += 1
			return self.create_sales_order(order)

	def refresh_order_statuses(self, window_start, window_end):
		orders = self.get_orders_instance()
		orders_payload = self.call_sp_api_method(
			sp_api_method=orders.get_orders,
			last_updated_after=format_local_as_amazon_utc(window_start),
			last_updated_before=format_local_as_amazon_utc(window_end),
			fulfillment_channels=["FBA", "SellerFulfilled"],
			max_results=100,
		)

		if orders_payload is None:
			return None

		updated_orders = []
		closed_orders = []
		pages_read = 0

		while orders_payload:
			orders_list = orders_payload.get("Orders", [])
			existing_orders = self.get_sales_orders_by_amazon_id(
				[order.get("AmazonOrderId") for order in orders_list]
			)

			for order in orders_list:
				order_id = order.get("AmazonOrderId")
				try:
					if self.sync_order(order, existing_orders.get(order_id)):
						updated_orders.append(order_id)
						if order.get("OrderStatus") in CLOSED_ORDER_STATUSES:
							closed_orders.append(order_id)
				except Exception:
					self.create_failed_sync_record(order_id or "Unknown", frappe.get_traceback(), order=order)

			# nosemgrep: frappe-manual-commit -- background job batch, keep progress if a later page fails
			frappe.db.commit()
			pages_read += 1

			next_token = orders_payload.get("NextToken")
			if not next_token:
				break

			if pages_read >= STATUS_REFRESH_MAX_PAGES:
				self.refresh_truncated = True
				alert_status_refresh_truncated(len(updated_orders), STATUS_REFRESH_MAX_PAGES)
				break

			orders_payload = self.call_sp_api_method(
				sp_api_method=orders.get_orders,
				next_token=next_token,
			)

			if not orders_payload:
				self.refresh_truncated = True
				break

		if closed_orders:
			alert_orders_canceled(closed_orders)

		return updated_orders

	def get_order(self, amazon_order_ids):
		orders = self.get_orders_instance()
		order_payload = self.call_sp_api_method(
			sp_api_method=orders.get_order,
			order_id=amazon_order_ids,
		)

		if not order_payload:
			return []

		existing_so = self.get_sales_orders_by_amazon_id([amazon_order_ids]).get(amazon_order_ids)
		if existing_so:
			sales_order = self.update_order_status(order_payload, existing_so) or existing_so.name
		else:
			sales_order = self.create_sales_order(order_payload)

		return [sales_order] if sales_order else []


def get_order_sync_watermark(run_started_at):
	return add_to_date(run_started_at, minutes=-(ORDER_SYNC_LAG_MINUTES + ORDER_SYNC_OVERLAP_MINUTES))


@frappe.whitelist()
def get_orders(created_after: str, sync_selected_date_only: int = 0):
	repository = AmazonRepository()
	run_started_at = now_datetime()
	result = repository.get_orders(created_after, sync_selected_date_only)

	if result is not None and not repository.sync_truncated and not cint(sync_selected_date_only):
		frappe.db.set_single_value(
			"Amazon SP API Settings", "last_synced_at", get_order_sync_watermark(run_started_at)
		)

	return result


@frappe.whitelist()
def get_order(amazon_order_ids: str):
	frappe.only_for("System Manager")

	repository = AmazonRepository()
	return repository.get_order(amazon_order_ids)


@frappe.whitelist()
def refresh_sales_order_status(sales_order: str):
	frappe.only_for("System Manager")

	amazon_order_id = frappe.db.get_value("Sales Order", sales_order, "amazon_order_id")
	if not amazon_order_id:
		frappe.throw(frappe._("{0} is not an Amazon order.").format(sales_order))

	repository = AmazonRepository()
	if not repository.get_order(amazon_order_id):
		frappe.throw(frappe._("Amazon did not return order {0}.").format(amazon_order_id))

	return frappe.db.get_value(
		"Sales Order", sales_order, ["amazon_order_status", "docstatus", "status"], as_dict=True
	)


def get_status_refresh_window(watermark, run_started_at):
	resume_from = get_datetime(watermark or run_started_at)
	backlog_floor = add_to_date(run_started_at, days=-STATUS_REFRESH_MAX_BACKLOG_DAYS)

	skipped_hours = max((backlog_floor - resume_from).total_seconds() / 3600, 0)

	latest_allowed = add_to_date(run_started_at, minutes=-STATUS_REFRESH_LAG_MINUTES)

	window_start = add_to_date(max(resume_from, backlog_floor), minutes=-STATUS_REFRESH_OVERLAP_MINUTES)
	window_end = min(add_to_date(window_start, hours=STATUS_REFRESH_WINDOW_HOURS), latest_allowed)

	return window_start, window_end, skipped_hours


@frappe.whitelist()
def refresh_order_statuses():
	frappe.only_for("System Manager")

	repository = AmazonRepository()
	run_started_at = now_datetime()
	window_start, window_end, skipped_hours = get_status_refresh_window(
		repository.amz_setting.last_status_synced_at, run_started_at
	)

	if skipped_hours:
		alert_status_refresh_gap(skipped_hours, window_start)

	result = repository.refresh_order_statuses(window_start, window_end)

	if result is not None and not repository.refresh_truncated:
		frappe.db.set_single_value("Amazon SP API Settings", "last_status_synced_at", window_end)

	return result


def schedule_refresh_order_statuses():
	if not frappe.db.get_single_value("Amazon SP API Settings", "enable_sync"):
		return

	frappe.enqueue(
		job_id="navgold.amazon_order_status_refresh",
		deduplicate=True,
		method=refresh_order_statuses,
		queue="long",
		timeout=6000,
	)
