from datetime import datetime

import frappe
from pytz import timezone


def format_date_time_to_ist(utc_time_str):
	# Parse the UTC time string
	utc_time = datetime.strptime(utc_time_str, "%Y-%m-%dT%H:%M:%SZ")

	# Convert to IST
	utc_zone = timezone("UTC")
	ist_zone = timezone("Asia/Kolkata")

	# Localize the UTC time
	utc_time = utc_zone.localize(utc_time)

	# Convert to IST
	ist_time = utc_time.astimezone(ist_zone)
	return ist_time.strftime("%Y-%m-%d %H:%M:%S")


def format_date_time_to_london(utc_time_str):
	# Parse the UTC time string
	utc_time = datetime.strptime(utc_time_str, "%Y-%m-%dT%H:%M:%SZ")

	# Convert to London time
	utc_zone = timezone("UTC")
	london_zone = timezone("Europe/London")

	# Localize the UTC time
	utc_time = utc_zone.localize(utc_time)

	# Convert to London time
	london_time = utc_time.astimezone(london_zone)
	return london_time.strftime("%Y-%m-%d %H:%M:%S")


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
