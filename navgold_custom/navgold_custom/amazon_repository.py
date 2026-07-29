import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import (
	add_days,
	add_to_date,
	convert_utc_to_system_timezone,
	flt,
	get_datetime,
	get_system_timezone,
	getdate,
	now_datetime,
)

from navgold_custom.navgold_custom.amazon_pricing.alerts import alert_order_canceled
from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import (
	Orders,
	SPAPIError,
)

# Overlap so a status change landing between two runs is never skipped.
STATUS_REFRESH_OVERLAP_MINUTES = 15
# Hard floor on the fetch window. A watermark that stops advancing (failed run, disabled sync) must
# not be able to grow the batch without bound — that is what tripped Amazon's 429 quota in 4f89d7c.
STATUS_REFRESH_MAX_WINDOW_HOURS = 24
STATUS_REFRESH_MAX_PAGES = 10

# Amazon suppresses ItemPrice and BuyerName until payment is authorised. PendingAvailability is the
# pre-order equivalent, so it carries the same missing data.
UNAUTHORISED_ORDER_STATUSES = ("Pending", "PendingAvailability")


def parse_amazon_utc(utc_time_str):
	utc_time = datetime.strptime(utc_time_str, "%Y-%m-%dT%H:%M:%SZ")
	local_time = convert_utc_to_system_timezone(utc_time)
	return local_time.strftime("%Y-%m-%d %H:%M:%S")


def format_local_as_amazon_utc(local_datetime):
	# Frappe datetimes are naive system-local; convert to real UTC so the offset
	# is not silently sent to Amazon as if it were already UTC.
	system_datetime = get_datetime(local_datetime).replace(tzinfo=ZoneInfo(get_system_timezone()))
	return system_datetime.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def clear_material_request_pending_flag(sales_order):
	material_requests = frappe.get_all(
		"Material Request Item",
		filters={"sales_order": sales_order, "docstatus": ["!=", 2]},
		pluck="parent",
		distinct=True,
	)

	for material_request in material_requests:
		frappe.db.set_value("Material Request", material_request, "custom_is_amazon_pending", 0)


class AmazonRepository:
	def __init__(self):
		self.refresh_truncated = False
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
			# A throttled/failed items call must not be swallowed as "no items" — raise so the
			# caller records a failed sync instead of silently dropping the order.
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
				# Throttled mid-pagination: the order is only partially read, so fail it
				# rather than building an incomplete item list.
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
				# Amazon can list an order before its items are queryable; record it so the
				# retry path recovers the order instead of losing it silently.
				self.create_failed_sync_record(order_id, "No order items returned by Amazon", order=order)
				return None

			if existing_so:
				sales_order = frappe.get_cached_doc("Sales Order", existing_so.name)
			else:
				sales_order = frappe.new_doc("Sales Order")

			customer_name = self.create_customer(order)
			self.create_address(order, customer_name)

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

			# ERPNext v17 moved set_taxes() off the controller into TaxService; fall back for older versions
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

			# Amazon withholds ItemPrice and BuyerName until payment is authorised, so submitting an
			# unauthorised order would freeze a £0 total and an order-id-shaped customer onto an
			# immutable doc. Park it in draft; refresh_order_statuses rebuilds and submits it once
			# the order moves on.
			if order.get("OrderStatus") not in UNAUTHORISED_ORDER_STATUSES:
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

	def get_orders(self, created_after, sync_selected_date_only=0):
		orders = self.get_orders_instance()
		order_statuses = [
			"Pending",
			"Unshipped",
			"PartiallyShipped",
			"Shipped",
			"InvoiceUnconfirmed",
			"Canceled",
			"Unfulfillable",
		]
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
			three_minutes_ago = now_datetime() - timedelta(minutes=3)
			created_before = min(end_of_day, three_minutes_ago)
			kwargs["created_before"] = format_local_as_amazon_utc(created_before)

		orders_payload = self.call_sp_api_method(sp_api_method=orders.get_orders, **kwargs)

		if orders_payload is None:
			return None

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

			frappe.db.commit()  # nosemgrep

			if not next_token:
				break

			orders_payload = self.call_sp_api_method(
				sp_api_method=orders.get_orders,
				created_after=kwargs["created_after"],
				next_token=next_token,
			)

		return sales_orders

	def update_order_status(self, order):
		order_id = order.get("AmazonOrderId")
		amazon_status = order.get("OrderStatus")

		existing_so = frappe.db.get_value(
			"Sales Order",
			{"amazon_order_id": order_id, "docstatus": ["!=", 2]},
			["name", "docstatus", "amazon_order_status"],
			as_dict=True,
		)

		# Orders we have never seen belong to the create path, not here.
		if not existing_so or existing_so.amazon_order_status == amazon_status:
			return None

		if amazon_status in ("Canceled", "Unfulfillable"):
			frappe.db.set_value("Sales Order", existing_so.name, "amazon_order_status", amazon_status)
			# A draft never reserved stock or raised a Material Request, so there is nothing to unwind.
			if existing_so.docstatus == 1:
				frappe.get_doc("Sales Order", existing_so.name).update_status("Closed")
			alert_order_canceled(existing_so.name, order_id, amazon_status)
			return existing_so.name

		# Payment cleared: Amazon will now return prices and a buyer name, so rebuild from the full
		# payload rather than relabelling a £0 draft.
		if existing_so.docstatus == 0:
			return self.create_sales_order(order)

		# A submitted order's money is settled — only the status field may move.
		frappe.db.set_value("Sales Order", existing_so.name, "amazon_order_status", amazon_status)
		clear_material_request_pending_flag(existing_so.name)

		return existing_so.name

	def refresh_order_statuses(self, last_updated_after):
		orders = self.get_orders_instance()
		orders_payload = self.call_sp_api_method(
			sp_api_method=orders.get_orders,
			last_updated_after=format_local_as_amazon_utc(last_updated_after),
			fulfillment_channels=["FBA", "SellerFulfilled"],
			max_results=50,
		)

		if orders_payload is None:
			return None

		updated_orders = []
		pages_read = 0

		while orders_payload:
			for order in orders_payload.get("Orders", []):
				try:
					if self.update_order_status(order):
						updated_orders.append(order.get("AmazonOrderId"))
				except Exception:
					order_id = (order or {}).get("AmazonOrderId", "Unknown")
					self.create_failed_sync_record(order_id, frappe.get_traceback(), order=order)

			frappe.db.commit()  # nosemgrep: manual-commit, commit each page so a later failure keeps progress
			pages_read += 1

			next_token = orders_payload.get("NextToken")
			if not next_token:
				break

			if pages_read >= STATUS_REFRESH_MAX_PAGES:
				# An unbounded LastUpdatedAfter walk is what tripped Amazon's 429 quota before. Stop
				# here and leave the watermark alone so the next run resumes from the same point.
				self.refresh_truncated = True
				frappe.log_error(
					f"Stopped at the {STATUS_REFRESH_MAX_PAGES}-page cap with {len(updated_orders)} "
					f"orders updated; the rest resume on the next run.",
					"Amazon Order Status Refresh",
				)
				break

			orders_payload = self.call_sp_api_method(
				sp_api_method=orders.get_orders,
				next_token=next_token,
			)

		return updated_orders

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
def get_orders(created_after: str, sync_selected_date_only: int = 0):
	repository = AmazonRepository()
	result = repository.get_orders(created_after, sync_selected_date_only)

	if result is not None:
		frappe.db.set_single_value("Amazon SP API Settings", "last_synced_at", frappe.utils.now_datetime())

	return result


@frappe.whitelist()
def get_order(amazon_order_ids: str):
	repository = AmazonRepository()
	return repository.get_order(amazon_order_ids)


@frappe.whitelist()
def refresh_order_statuses():
	repository = AmazonRepository()
	run_started_at = now_datetime()

	watermark = repository.amz_setting.last_status_synced_at or run_started_at
	window_start = max(
		add_to_date(get_datetime(watermark), minutes=-STATUS_REFRESH_OVERLAP_MINUTES),
		add_to_date(run_started_at, hours=-STATUS_REFRESH_MAX_WINDOW_HOURS),
	)

	result = repository.refresh_order_statuses(window_start)

	# Only a pass that both succeeded and read every page may move the watermark forward.
	if result is not None and not repository.refresh_truncated:
		frappe.db.set_single_value("Amazon SP API Settings", "last_status_synced_at", run_started_at)

	return result


def schedule_refresh_order_statuses():
	if not frappe.db.get_single_value("Amazon SP API Settings", "enable_sync"):
		return

	frappe.enqueue(
		job_id="navgold.amazon_order_status_refresh",
		deduplicate=True,
		method=refresh_order_statuses,
		timeout=6000,
	)
