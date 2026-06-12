import frappe
from frappe.query_builder import DocType, Order
from frappe.query_builder.functions import Sum
from frappe.utils import flt
from frappe.utils.caching import redis_cache

BATCH_SIZE = 500
ITEM_AFN_FIELD = "custom_amazon_afn_stock"


def bulk_set_value(doctype: str, names: list, field: str, value, batch_size: int = BATCH_SIZE):
	for i in range(0, len(names), batch_size):
		batch = names[i : i + batch_size]
		frappe.db.set_value(doctype, {"name": ["in", batch]}, field, value)


def get_afn_warehouse_stock_map(item_codes: list[str], warehouse: str) -> dict[str, float]:
	if not item_codes or not warehouse:
		return {}

	bin_dt = DocType("Bin")
	rows = (
		frappe.qb.from_(bin_dt)
		.select(bin_dt.item_code, Sum(bin_dt.actual_qty).as_("actual_qty"))
		.where((bin_dt.item_code.isin(item_codes)) & (bin_dt.warehouse == warehouse))
		.groupby(bin_dt.item_code)
	).run(as_dict=True)

	return {row.item_code: flt(row.actual_qty) for row in rows}


def get_fba_stock_map(item_codes: list[str], afn_warehouse: str | None) -> dict[str, float]:
	if not item_codes:
		return {}

	bin_stock_map = get_afn_warehouse_stock_map(item_codes, afn_warehouse)

	if not frappe.get_cached_doc("Navgold Settings").enable_afn_stock_sync:
		return bin_stock_map

	fetched_rows = frappe.get_all(
		"Item",
		filters={"name": ["in", item_codes]},
		fields=["name", ITEM_AFN_FIELD],
	)
	fetched_stock_map = {row.name: flt(row.get(ITEM_AFN_FIELD)) for row in fetched_rows}

	# FBA Transfer stock entries land in the AFN warehouse Bin at packing time, before Amazon's
	# inventory report counts them as fulfillable. Taking the max keeps the FBA strategy selected
	# during the inbound transit window while still seeing stock only Amazon knows about.
	return {
		item_code: max(flt(bin_stock_map.get(item_code)), fetched_stock_map.get(item_code, 0.0))
		for item_code in item_codes
	}


def get_restock_data_map(item_codes: list[str]) -> dict[str, object]:
	if not item_codes:
		return {}

	purchase_order = DocType("Purchase Order")
	purchase_order_item = DocType("Purchase Order Item")
	rows = (
		frappe.qb.from_(purchase_order)
		.join(purchase_order_item)
		.on(purchase_order.name == purchase_order_item.parent)
		.select(purchase_order_item.item_code, purchase_order_item.schedule_date)
		.where(
			(purchase_order.docstatus == 1)
			& (purchase_order.status.notin(["Closed", "Cancelled", "Completed"]))
			& (purchase_order_item.item_code.isin(item_codes))
		)
		.orderby(purchase_order_item.schedule_date, order=Order.desc)
	).run(as_dict=True)

	restock_map: dict[str, object] = {}
	for row in rows:
		if row.item_code not in restock_map:
			restock_map[row.item_code] = row.schedule_date
	return restock_map


def get_free_qty_map(item_codes: list[str]) -> dict[str, dict[str, float]]:
	if not item_codes:
		return {}

	bin_dt = DocType("Bin")
	warehouse_dt = DocType("Warehouse")

	result = (
		frappe.qb.from_(bin_dt)
		.left_join(warehouse_dt)
		.on(bin_dt.warehouse == warehouse_dt.name)
		.select(
			bin_dt.item_code,
			Sum(bin_dt.actual_qty).as_("actual_qty"),
			(Sum(bin_dt.actual_qty) - Sum(bin_dt.reserved_qty)).as_("free_qty"),
		)
		.where((bin_dt.item_code.isin(item_codes)) & (warehouse_dt.is_rejected_warehouse == 0))
		.groupby(bin_dt.item_code)
	).run(as_dict=True)

	return {
		r["item_code"]: {
			"actual_qty": flt(r.get("actual_qty", 0)),
			"free_qty": flt(r.get("free_qty", 0)),
		}
		for r in result
	}


@redis_cache()
@frappe.whitelist()
def get_conversion_factor(item_code: str, uom: str):
	if not uom or uom == "Nos":
		return 1.0
	conversion_factor = frappe.db.get_value(
		"UOM Conversion Detail", {"parent": item_code, "uom": uom}, "conversion_factor"
	)
	return flt(conversion_factor) or 1.0


def assert_amazon_outbound_not_frozen():
	# Deliberately uncached: this is the emergency kill switch and must take effect
	# on long-running workers without a cache clear.
	if frappe.db.get_single_value("Navgold Settings", "freeze_amazon_outbound"):
		frappe.throw(frappe._("Amazon outbound writes are frozen via Navgold Settings."))
