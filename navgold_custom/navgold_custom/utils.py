import frappe
from frappe.query_builder import DocType
from frappe.query_builder.functions import Sum
from frappe.utils import flt


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
