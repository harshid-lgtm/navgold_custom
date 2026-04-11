# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt
import frappe
from frappe import _
from frappe.query_builder import DocType, Order
from frappe.query_builder.functions import Sum
from frappe.utils import ceil, flt
from frappe.utils.caching import redis_cache

from navgold_custom.navgold_custom.utils import get_free_qty_map

material_request_status_list = ["Pending", "Partially Ordered", "Partially Received"]


def execute(filters: dict | None = None):
	data = get_data(filters)
	columns = get_columns(data)
	return columns, data


def get_columns(data: list[dict]) -> list[dict]:
	columns = [
		{
			"label": _("History"),
			"fieldname": "po_history",
			"fieldtype": "Data",
			"width": 40,
		},
		{
			"label": _("Item Code"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 150,
		},
		{
			"label": _("Description"),
			"fieldname": "description",
			"fieldtype": "Data",
			"width": 150,
		},
		{
			"label": _("Actual Qty"),
			"fieldname": "actual_qty",
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"label": _("Free Qty"),
			"fieldname": "free_qty",
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"label": _("Total Required Qty"),
			"fieldname": "total_required_qty",
			"fieldtype": "Float",
			"width": 150,
		},
		{
			"label": _("Supplier"),
			"fieldname": "supplier",
			"fieldtype": "Link",
			"options": "Supplier",
			"width": 200,
		},
		{
			"label": _("Rank"),
			"fieldname": "supplier_rank",
			"fieldtype": "Int",
			"width": 200,
		},
		{
			"label": _("Supplier UOM"),
			"fieldname": "supplier_uom",
			"fieldtype": "Link",
			"options": "UOM",
			"width": 150,
		},
		{
			"label": _("Supplier Stock"),
			"fieldname": "available_stock",
			"fieldtype": "Float",
			"width": 150,
		},
		{
			"label": _("Allocated Qty"),
			"fieldname": "allocated_qty",
			"fieldtype": "Float",
			"width": 150,
		},
		{
			"label": _("Remaining Qty"),
			"fieldname": "remaining_qty",
			"fieldtype": "Float",
			"width": 150,
		},
		{
			"label": _("Unit Price"),
			"fieldname": "unit_price",
			"fieldtype": "Currency",
			"width": 150,
		},
		{
			"label": _("Last Purchase Rate"),
			"fieldname": "last_purchase_rate",
			"fieldtype": "Data",
			"width": 160,
		},
		{
			"label": _("Reference"),
			"fieldname": "ref_doc",
			"fieldtype": "Link",
			"options": "Supplier Item Price and Stock",
			"width": 150,
		},
	]
	# Dynamically add offer columns (Child table rows)
	max_offers = 0
	for row in data:
		if row.get("offers"):
			max_offers = max(max_offers, len(row["offers"]))

	for i in range(max_offers):
		if i == 0:
			month_label = "This Month"
		elif i == 1:
			month_label = "Next Month"
		else:
			month_label = f"In {i} Months"

		columns.append(
			{
				"label": _(month_label),
				"fieldname": f"offer_{i}",
				"fieldtype": "Data",
				"width": 150,
			}
		)

	return columns


# Adds supplier to list until the required qty is satified
def get_data(filters):
	material_request = DocType("Material Request")
	material_request_item = DocType("Material Request Item")
	supplier_item_price_and_stock = DocType("Supplier Item Price and Stock")
	item_doc = DocType("Item")
	items_requests = (
		frappe.qb.from_(material_request)
		.left_join(material_request_item)
		.on(material_request_item.parent == material_request.name)
		.select(
			material_request_item.item_code,
			material_request_item.stock_qty.as_("required_qty"),
			material_request_item.ordered_qty,
		)
		.where(
			(material_request.material_request_type == "Purchase")
			& (material_request.status.isin(material_request_status_list))
			& (material_request.custom_is_amazon_pending == frappe.cint(filters.get("is_amazon_pending", 0)))
		)
	).run(as_dict=True)

	items_needed = {}
	for r in items_requests:
		item = r.get("item_code")
		if not item:
			continue

		required = flt(r.get("required_qty") or 0)
		ordered = flt(r.get("ordered_qty") or 0)
		pending = max(0, required - ordered)

		if pending > 0:
			items_needed[item] = items_needed.get(item, 0) + pending
	if not items_needed:
		return []
	supplier_list_query = (
		frappe.qb.from_(supplier_item_price_and_stock)
		.left_join(item_doc)
		.on(supplier_item_price_and_stock.item == item_doc.name)
		.select(
			supplier_item_price_and_stock.item,
			item_doc.description,
			supplier_item_price_and_stock.supplier,
			supplier_item_price_and_stock.price.as_("unit_price"),
			supplier_item_price_and_stock.stock_qty,
			supplier_item_price_and_stock.uom,
			supplier_item_price_and_stock.name,
			supplier_item_price_and_stock.rank,
		)
		.where(
			(supplier_item_price_and_stock.purchase_blocked != 1)
			& (supplier_item_price_and_stock.stock_qty > 0)
			& (supplier_item_price_and_stock.item.isin(list(items_needed)))
		)
	)

	if filters.get("supplier"):
		supplier_list_query = supplier_list_query.where(
			supplier_item_price_and_stock.supplier == filters.get("supplier")
		)

	supplier_list = supplier_list_query.orderby(
		supplier_item_price_and_stock.item, supplier_item_price_and_stock.unit_price
	).run(as_dict=True)

	if supplier_list:
		offers_by_parent = get_offers_by_supplier(supplier_list)

	last_purchase_rate_map = get_last_purchase_rate_map(supplier_list) if supplier_list else {}

	free_qty_map = get_free_qty_map(list(items_needed.keys()))

	results = []

	item_remaining = {item: qty for item, qty in items_needed.items()}

	for s in supplier_list:
		item_code = s["item"]

		remaining = item_remaining.get(item_code, 0)
		if remaining <= 0:
			continue

		total_required = items_needed[item_code]
		available_stock = s["stock_qty"]

		allocated = min(available_stock, remaining)

		item_remaining[item_code] = remaining - allocated

		offers = offers_by_parent.get(s["name"], [])

		item_qty_data = free_qty_map.get(item_code, {})
		row = {
			"po_history": "",
			"item_code": item_code,
			"description": s["description"],
			"actual_qty": item_qty_data.get("actual_qty", 0),
			"free_qty": item_qty_data.get("free_qty", 0),
			"total_required_qty": total_required,
			"supplier": s["supplier"],
			"supplier_uom": s["uom"],
			"available_stock": available_stock,
			"allocated_qty": allocated,
			"remaining_qty": remaining - allocated,
			"unit_price": s["unit_price"],
			"last_purchase_rate": last_purchase_rate_map.get((item_code, s["supplier"]), "NA"),
			"ref_doc": s["name"],
			"supplier_rank": s["rank"],
			"offers": offers,
		}
		for i, offer in enumerate(offers):
			row[f"offer_{i}"] = offer

		results.append(row)

	return results


# This is a 3 step process
# 1. Based on supplier group items
# 2. Based on items get the pending MR
# 3. Based on each MR create a new line item in PO


@frappe.whitelist()
def create_purchase_orders_from_report(selected_items: str | list[dict]):
	if isinstance(selected_items, str):
		selected_items = frappe.parse_json(selected_items)

	if not selected_items:
		frappe.throw(_("No items selected"))

	supplier_groups = {}
	for item in selected_items:
		supplier = item.get("supplier")
		if not supplier:
			continue

		if supplier not in supplier_groups:
			supplier_groups[supplier] = {"items": []}

		supplier_groups[supplier]["items"].append(item)

	created_pos = []

	for supplier, data in supplier_groups.items():
		try:
			po = create_purchase_order_with_mr_allocation(supplier, data)
			if po:
				created_pos.append(po.name)

		except Exception as e:
			frappe.log_error(f"Error creating PO for {supplier}: {e!s}")
			frappe.throw(_("Error creating Purchase Order for {0}: {1}").format(supplier, str(e)))

	return {
		"success": True,
		"purchase_orders": created_pos,
		"message": _("Created {0} Purchase Order(s)").format(len(created_pos)),
	}


def create_purchase_order_with_mr_allocation(supplier, data):
	items_to_order = [item.get("item_code") for item in data["items"]]

	material_request = DocType("Material Request")
	material_request_item = DocType("Material Request Item")

	mr_items = (
		frappe.qb.from_(material_request)
		.left_join(material_request_item)
		.on(material_request_item.parent == material_request.name)
		.select(
			material_request_item.item_code,
			material_request_item.stock_qty.as_("required_qty"),
			material_request_item.ordered_qty,
			material_request_item.name.as_("material_request_item"),
			material_request_item.parent.as_("material_request"),
			material_request_item.schedule_date,
		)
		.where(
			(material_request.material_request_type == "Purchase")
			& (material_request.status.isin(material_request_status_list))
			& (material_request_item.item_code.isin(items_to_order))
		)
		.orderby(material_request_item.schedule_date)
	).run(as_dict=True)

	amz_setting = frappe.get_single("Amazon SP API Settings")
	warehouse = amz_setting.warehouse
	mr_by_item = {}
	company = amz_setting.company

	for mr in mr_items:
		item = mr["item_code"]
		pending = max(0, flt(mr["required_qty"]) - flt(mr["ordered_qty"]))

		if pending <= 0:
			continue

		if item not in mr_by_item:
			mr_by_item[item] = []

		mr_by_item[item].append(
			{
				"material_request": mr["material_request"],
				"material_request_item": mr["material_request_item"],
				"pending_qty": pending,
				"schedule_date": mr["schedule_date"],
			}
		)

	po = frappe.new_doc("Purchase Order")
	po.supplier = supplier
	po.company = company
	po.currency = frappe.db.get_default("currency")
	po.transaction_date = frappe.utils.today()

	earliest_date = None

	# Based on each item and MR create a line item though n^2 but not many
	for item_data in data["items"]:
		item_code = item_data.get("item_code")
		available_stock = flt(item_data.get("available_stock", 0))
		allocated_qty = flt(item_data.get("allocated_qty", 0))
		supplier_uom = item_data.get("supplier_uom")
		unit_price = flt(item_data.get("unit_price", 0))

		conversion_factor = get_conversion_factor(item_code, supplier_uom)

		mrs_for_item = mr_by_item.get(item_code, [])
		if not mrs_for_item:
			continue

		allocated_qty_in_supplier_uom = (
			ceil(allocated_qty / conversion_factor) if conversion_factor > 0 else allocated_qty
		)

		supplier_uom_remaining = allocated_qty_in_supplier_uom

		for mr in mrs_for_item:
			if supplier_uom_remaining <= 0:
				break

			mr_pending_in_supplier_uom = (
				ceil(mr["pending_qty"] / conversion_factor) if conversion_factor > 0 else mr["pending_qty"]
			)

			qty_to_order = min(mr_pending_in_supplier_uom, supplier_uom_remaining)

			if qty_to_order <= 0:
				continue

			schedule_date = mr["schedule_date"] or frappe.utils.today()
			if not isinstance(schedule_date, str):
				schedule_date = str(schedule_date)

			today_str = frappe.utils.today()

			if schedule_date < today_str:
				schedule_date = today_str

			if not earliest_date or schedule_date < earliest_date:
				earliest_date = schedule_date

			po.append(
				"items",
				{
					"item_code": item_code,
					"qty": qty_to_order,
					"uom": supplier_uom,
					"stock_uom": supplier_uom,
					"conversion_factor": conversion_factor,
					"rate": unit_price,
					"schedule_date": schedule_date,
					"warehouse": warehouse,
					"material_request": mr["material_request"],
					"material_request_item": mr["material_request_item"],
				},
			)
			supplier_uom_remaining -= qty_to_order

		total_ordered = allocated_qty_in_supplier_uom - supplier_uom_remaining
		new_stock = max(0, available_stock - total_ordered)
		update_supplier_stock_after_po(supplier, supplier_uom, item_code, new_stock)

	po.schedule_date = earliest_date or frappe.utils.today()

	if po.items:
		po.insert(ignore_permissions=True)
		po.submit()
		return po

	return None


@redis_cache()
def get_conversion_factor(item, supplier_uom):
	conv = frappe.db.get_value(
		"UOM Conversion Detail", {"parent": item, "uom": supplier_uom}, "conversion_factor"
	)
	return conv or 1


# Cannot use set_value doc_events 👀
def update_supplier_stock_after_po(supplier, supplier_uom, item_code, remaining_stock):
	supplier_item_price_and_stock = frappe.get_cached_doc(
		"Supplier Item Price and Stock", {"supplier": supplier, "item": item_code, "uom": supplier_uom}
	)
	supplier_item_price_and_stock.stock_qty = remaining_stock
	supplier_item_price_and_stock.save()


def get_last_purchase_rate_map(supplier_list):
	if not supplier_list:
		return {}

	po = DocType("Purchase Order")
	po_item = DocType("Purchase Order Item")

	items = list({s["item"] for s in supplier_list})
	suppliers = list({s["supplier"] for s in supplier_list})

	results = (
		frappe.qb.from_(po)
		.left_join(po_item)
		.on(po_item.parent == po.name)
		.select(
			po_item.item_code,
			po.supplier,
			po_item.rate,
			po.transaction_date,
		)
		.where((po.docstatus == 1) & (po_item.item_code.isin(items)) & (po.supplier.isin(suppliers)))
		.orderby(po.transaction_date, order=Order.desc)
		.orderby(po.creation, order=Order.desc)
	).run(as_dict=True)

	rate_map = {}
	for r in results:
		key = (r["item_code"], r["supplier"])
		if key not in rate_map:
			rate_map[key] = r["rate"]

	return rate_map


def get_offers_by_supplier(supplier_list):
	supplier_offer = DocType("Supplier Offer")
	offers_by_parent = {}
	parent_names = [s["name"] for s in supplier_list]
	offers_data = (
		frappe.qb.from_(supplier_offer)
		.select(supplier_offer.parent, supplier_offer.offer, supplier_offer.idx)
		.where(supplier_offer.parent.isin(parent_names))
		.orderby(supplier_offer.parent, supplier_offer.idx)
	).run(as_dict=True)

	for offer in offers_data:
		parent = offer["parent"]
		if parent not in offers_by_parent:
			offers_by_parent[parent] = []
		offers_by_parent[parent].append(offer["offer"])
	return offers_by_parent


@frappe.whitelist()
def get_po_history_for_item(item_code: str) -> list[dict]:
	if not item_code:
		return []

	po = DocType("Purchase Order")
	po_item = DocType("Purchase Order Item")

	result = (
		frappe.qb.from_(po)
		.left_join(po_item)
		.on(po_item.parent == po.name)
		.select(
			po.transaction_date,
			po.name.as_("purchase_order"),
			po.supplier,
			po_item.item_code,
			po.status,
			Sum(po_item.qty).as_("qty"),
			Sum(po_item.qty * po_item.rate).as_("amount"),
		)
		.where((po_item.item_code == item_code) & (po.docstatus == 1))
		.groupby(po.name, po_item.item_code)
		.orderby(po.transaction_date, order=Order.desc)
		.limit(50)
	).run(as_dict=True)

	return result
