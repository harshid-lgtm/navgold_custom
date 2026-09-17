# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt
import frappe
from frappe import _
from frappe.query_builder import DocType, Order
from frappe.query_builder.functions import Sum
from frappe.utils import ceil, cint, flt

from navgold_custom.navgold_custom.utils import get_conversion_factor, get_free_qty_map

material_request_status_list = ["Pending", "Partially Ordered", "Partially Received"]


def execute(filters: dict | None = None):
	rows, max_supplier_offers, reference_suppliers = get_data(filters)
	columns = get_columns(max_supplier_offers, reference_suppliers)
	return columns, rows


@frappe.whitelist()
def reconcile_demand_now() -> dict[str, int]:
	from navgold_custom.navgold_custom.custom_script.material_request.material_request import (
		reconcile_purchase_demand_job,
	)

	frappe.has_permission("Material Request", "write", throw=True)

	result = reconcile_purchase_demand_job()
	if result is None:
		frappe.throw(_("Purchase demand is already being reconciled. Try again in a moment."))

	return {"retired": len(result["retired"]), "requested": len(result["requested"])}


def get_columns(max_supplier_offers: int, reference_suppliers: list[dict]) -> list[dict]:
	columns = get_static_columns()
	columns += build_offer_columns(max_supplier_offers, fieldname_prefix="offer")
	for reference_supplier in reference_suppliers:
		columns += build_offer_columns(
			reference_supplier["max_offers"],
			fieldname_prefix=f"{reference_supplier['field_prefix']}_offer",
			label_prefix=reference_supplier["display_name"],
		)
	return columns


def get_static_columns() -> list[dict]:
	return [
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


def build_offer_columns(count: int, fieldname_prefix: str, label_prefix: str = "") -> list[dict]:
	columns = []
	for i in range(count):
		month_label = _(get_month_label(i))
		label = f"{label_prefix}: {month_label}" if label_prefix else month_label
		columns.append(
			{
				"label": label,
				"fieldname": f"{fieldname_prefix}_{i}",
				"fieldtype": "Data",
				"width": 180 if label_prefix else 150,
			}
		)
	return columns


def get_month_label(index: int) -> str:
	if index == 0:
		return "This Month"
	if index == 1:
		return "Next Month"
	return f"In {index} Months"


# Adds supplier to list in price-rank order until each item's required qty is satisfied
def get_data(filters):
	items_needed = get_items_needed(frappe.cint(filters.get("is_amazon_pending", 0)))
	if not items_needed:
		return [], 0, []

	item_codes = list(items_needed.keys())
	supplier_list = get_supplier_list(item_codes, filters.get("supplier"))

	offers_by_parent = get_offers_by_supplier(supplier_list)
	last_purchase_rate_map = get_last_purchase_rate_map(supplier_list)
	reference_suppliers = get_reference_supplier_offers(item_codes)
	# purchasing must see committed demand: a draft order is stock we no longer get to sell
	free_qty_map = get_free_qty_map(item_codes, deduct_draft_orders=True)

	results = []
	max_supplier_offers = 0
	item_remaining = dict(items_needed)

	for supplier_item in supplier_list:
		item_code = supplier_item["item"]
		remaining = item_remaining.get(item_code, 0)
		if remaining <= 0:
			continue

		available_stock = supplier_item["stock_qty"]
		allocated = min(available_stock, remaining)
		item_remaining[item_code] = remaining - allocated

		offers = offers_by_parent.get(supplier_item["name"], [])
		if len(offers) > max_supplier_offers:
			max_supplier_offers = len(offers)

		item_qty_data = free_qty_map.get(item_code, {})
		row = {
			"po_history": "",
			"item_code": item_code,
			"description": supplier_item["description"],
			"actual_qty": item_qty_data.get("actual_qty", 0),
			"free_qty": item_qty_data.get("free_qty", 0),
			"total_required_qty": items_needed[item_code],
			"supplier": supplier_item["supplier"],
			"supplier_uom": supplier_item["uom"],
			"available_stock": available_stock,
			"allocated_qty": allocated,
			"remaining_qty": remaining - allocated,
			"unit_price": supplier_item["unit_price"],
			"last_purchase_rate": last_purchase_rate_map.get((item_code, supplier_item["supplier"]), "NA"),
			"ref_doc": supplier_item["name"],
			"supplier_rank": supplier_item["rank"],
		}
		for i, offer in enumerate(offers):
			row[f"offer_{i}"] = offer
		# Nesting is fine as we will only have 1 reference supplier with 3 offer rows at max - it's almost O(n)
		for reference_supplier in reference_suppliers:
			for i, offer in enumerate(reference_supplier["offers_by_item"].get(item_code, [])):
				row[f"{reference_supplier['field_prefix']}_offer_{i}"] = offer

		results.append(row)

	return results, max_supplier_offers, reference_suppliers


def get_items_needed(is_amazon_pending: int) -> dict:
	material_request = DocType("Material Request")
	material_request_item = DocType("Material Request Item")

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
			& (material_request.custom_is_amazon_pending == is_amazon_pending)
		)
	).run(as_dict=True)

	items_needed = {}
	for row in items_requests:
		item_code = row.get("item_code")
		if not item_code:
			continue

		required = flt(row.get("required_qty") or 0)
		ordered = flt(row.get("ordered_qty") or 0)
		pending = max(0, required - ordered)

		if pending > 0:
			items_needed[item_code] = items_needed.get(item_code, 0) + pending

	return items_needed


def get_supplier_list(item_codes: list[str], supplier_filter: str | None) -> list[dict]:
	supplier_doc = DocType("Supplier")
	supplier_item_price_and_stock = DocType("Supplier Item Price and Stock")
	item_doc = DocType("Item")

	query = (
		frappe.qb.from_(supplier_item_price_and_stock)
		.left_join(item_doc)
		.on(supplier_item_price_and_stock.item == item_doc.name)
		.left_join(supplier_doc)
		.on(supplier_item_price_and_stock.supplier == supplier_doc.name)
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
			& (supplier_item_price_and_stock.item.isin(item_codes))
			& (supplier_doc.custom_is_reference_only != 1)
		)
	)

	if supplier_filter:
		query = query.where(supplier_item_price_and_stock.supplier == supplier_filter)

	return query.orderby(supplier_item_price_and_stock.item, supplier_item_price_and_stock.rank).run(
		as_dict=True
	)


# This is a 3 step process
# 1. Based on supplier group items
# 2. Based on items get the pending MR
# 3. Based on each MR create a new line item in PO


@frappe.whitelist()
def create_purchase_orders_from_report(selected_items: str | list[dict], is_amazon_pending: int = 0):
	if isinstance(selected_items, str):
		selected_items = frappe.parse_json(selected_items)

	is_amazon_pending = cint(is_amazon_pending)

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
			po = create_purchase_order_with_mr_allocation(supplier, data, is_amazon_pending)
			if po:
				created_pos.append(po.name)

		except Exception as error:
			frappe.log_error(f"Error creating PO for {supplier}: {error!s}")
			frappe.throw(_("Error creating Purchase Order for {0}: {1}").format(supplier, str(error)))

	return {
		"success": True,
		"purchase_orders": created_pos,
		"message": _("Created {0} Purchase Order(s)").format(len(created_pos)),
	}


def create_purchase_order_with_mr_allocation(supplier, data, is_amazon_pending=0):
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
			& (material_request.custom_is_amazon_pending == is_amazon_pending)
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

	stock_uom_by_item = dict(
		frappe.get_all(
			"Item", filters={"name": ("in", items_to_order)}, fields=["name", "stock_uom"], as_list=True
		)
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

		pack_size = get_conversion_factor(item_code, supplier_uom)

		mrs_for_item = mr_by_item.get(item_code, [])
		if not mrs_for_item:
			continue

		# Ordered in single units, still in whole supplier packs: a short delivery is then booked in
		# as a plain unit count instead of a fraction of a pack. The item is rounded once here rather
		# than each request on its own, which used to reach for a spare pack per request.
		allocated_units = ceil(allocated_qty / pack_size) * pack_size
		units_remaining = allocated_units
		last_schedule_date = None

		for mr in mrs_for_item:
			if units_remaining <= 0:
				break

			qty_to_order = min(flt(mr["pending_qty"]), units_remaining)

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
					"uom": stock_uom_by_item.get(item_code),
					"stock_uom": stock_uom_by_item.get(item_code),
					"conversion_factor": 1,
					"rate": unit_price,
					"schedule_date": schedule_date,
					"warehouse": warehouse,
					"material_request": mr["material_request"],
					"material_request_item": mr["material_request_item"],
				},
			)
			units_remaining -= qty_to_order
			last_schedule_date = schedule_date

		# The units that fill the last pack answer no request, so they ride a line of their own rather
		# than pushing a request past what it asked for and leaning on the over-order allowance.
		if last_schedule_date and units_remaining > 0:
			po.append(
				"items",
				{
					"item_code": item_code,
					"qty": units_remaining,
					"uom": stock_uom_by_item.get(item_code),
					"stock_uom": stock_uom_by_item.get(item_code),
					"conversion_factor": 1,
					"rate": unit_price,
					"schedule_date": last_schedule_date,
					"warehouse": warehouse,
				},
			)
			units_remaining = 0

		total_ordered = allocated_units - units_remaining
		new_stock = max(0, available_stock - total_ordered)
		update_supplier_stock_after_po(supplier, supplier_uom, item_code, new_stock)

	po.schedule_date = earliest_date or frappe.utils.today()

	if po.items:
		po.insert(ignore_permissions=True)
		po.submit()
		return po

	return None


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

	items = list({supplier_item["item"] for supplier_item in supplier_list})
	suppliers = list({supplier_item["supplier"] for supplier_item in supplier_list})

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
	for row in results:
		key = (row["item_code"], row["supplier"])
		if key not in rate_map:
			rate_map[key] = row["rate"]

	return rate_map


def get_offers_by_supplier(supplier_list):
	if not supplier_list:
		return {}

	supplier_offer = DocType("Supplier Offer")
	parent_names = [supplier_item["name"] for supplier_item in supplier_list]
	offers_data = (
		frappe.qb.from_(supplier_offer)
		.select(supplier_offer.parent, supplier_offer.offer, supplier_offer.idx)
		.where(supplier_offer.parent.isin(parent_names))
		.orderby(supplier_offer.parent, supplier_offer.idx)
	).run(as_dict=True)

	offers_by_parent = {}
	for offer in offers_data:
		parent = offer["parent"]
		if parent not in offers_by_parent:
			offers_by_parent[parent] = []
		offers_by_parent[parent].append(offer["offer"])
	return offers_by_parent


def get_reference_supplier_offers(item_codes):
	if not item_codes:
		return []

	supplier_doc = DocType("Supplier")
	supplier_item_price_and_stock = DocType("Supplier Item Price and Stock")
	supplier_offer = DocType("Supplier Offer")

	reference_records = (
		frappe.qb.from_(supplier_item_price_and_stock)
		.left_join(supplier_doc)
		.on(supplier_item_price_and_stock.supplier == supplier_doc.name)
		.select(
			supplier_item_price_and_stock.name,
			supplier_item_price_and_stock.item,
			supplier_item_price_and_stock.supplier,
			supplier_doc.supplier_name,
		)
		.where(
			(supplier_item_price_and_stock.item.isin(item_codes))
			& (supplier_doc.custom_is_reference_only == 1)
		)
	).run(as_dict=True)

	if not reference_records:
		return []

	parent_meta = {
		row["name"]: {
			"supplier": row["supplier"],
			"item": row["item"],
			"display_name": row["supplier_name"] or row["supplier"],
		}
		for row in reference_records
	}

	offers_data = (
		frappe.qb.from_(supplier_offer)
		.select(supplier_offer.parent, supplier_offer.offer, supplier_offer.idx)
		.where(supplier_offer.parent.isin(list(parent_meta.keys())))
		.orderby(supplier_offer.parent, supplier_offer.idx)
	).run(as_dict=True)

	by_supplier = {}
	for _parent_name, meta in parent_meta.items():
		supplier = meta["supplier"]
		if supplier not in by_supplier:
			by_supplier[supplier] = {
				"supplier": supplier,
				"display_name": meta["display_name"],
				"offers_by_item": {},
				"max_offers": 0,
			}

	for offer in offers_data:
		meta = parent_meta.get(offer["parent"])
		if not meta:
			continue
		bucket = by_supplier[meta["supplier"]]
		item_offers = bucket["offers_by_item"].setdefault(meta["item"], [])
		item_offers.append(offer["offer"])
		if len(item_offers) > bucket["max_offers"]:
			bucket["max_offers"] = len(item_offers)

	reference_suppliers = [bucket for bucket in by_supplier.values() if bucket["max_offers"] > 0]
	reference_suppliers.sort(key=lambda bucket: bucket["display_name"])

	for index, bucket in enumerate(reference_suppliers):
		bucket["field_prefix"] = f"ref{index}"

	return reference_suppliers


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
