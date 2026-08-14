# Copyright (c) 2026, Navgold and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder.functions import Avg, Count, Date, Max, Min
from frappe.utils import add_days, cint, flt, today

DEFAULT_REQUIRED_DAYS = 0


def execute(filters=None):
	filters = frappe._dict(filters or {})
	required_days = cint(filters.get("required_days") or DEFAULT_REQUIRED_DAYS)
	from_date = add_days(today(), -required_days)

	return get_columns(), get_data(from_date)


def get_data(from_date):
	packing_slip = frappe.qb.DocType("Packing Slip")
	pack_date = Date(packing_slip.creation).as_("pack_date")

	# ponytail: one packing slip per delivery note today, so a slip count is an order count;
	# switch to a distinct delivery note count if multi-parcel packing ever starts
	rows = (
		frappe.qb.from_(packing_slip)
		.select(
			pack_date,
			packing_slip.owner.as_("packer"),
			Count("*").as_("orders_packed"),
			Avg(packing_slip.net_weight_pkg).as_("avg_weight"),
			Min(packing_slip.creation).as_("first_pack"),
			Max(packing_slip.creation).as_("last_pack"),
		)
		.where((packing_slip.docstatus == 1) & (Date(packing_slip.creation) >= from_date))
		.groupby(pack_date, packing_slip.owner)
		.orderby(pack_date, order=frappe.qb.desc)
		.orderby(packing_slip.owner)
	).run(as_dict=True)

	packer_names = get_packer_names(rows)

	for row in rows:
		row.packer_name = packer_names.get(row.packer, row.packer)
		row.avg_weight = flt(row.avg_weight, 2)
		row.hours = get_worked_hours(row.first_pack, row.last_pack)
		# a single slip spans no time, so any rate we printed would be invented
		row.orders_packed_per_hour = flt(row.orders_packed / row.hours, 2) if row.hours else None

	return rows


def get_packer_names(rows):
	packers = {row.packer for row in rows if row.packer}
	if not packers:
		return {}

	users = frappe.get_all("User", filters={"name": ["in", list(packers)]}, fields=["name", "full_name"])
	return {user.name: user.full_name or user.name for user in users}


def get_worked_hours(first_pack, last_pack):
	if not first_pack or not last_pack:
		return 0

	return flt((last_pack - first_pack).total_seconds() / 3600, 2)


def get_columns():
	return [
		{
			"fieldname": "pack_date",
			"label": _("Date"),
			"fieldtype": "Date",
			"width": 110,
		},
		{
			"fieldname": "packer",
			"label": _("Packer"),
			"fieldtype": "Link",
			"options": "User",
			"width": 200,
		},
		{
			"fieldname": "packer_name",
			"label": _("Packer Name"),
			"fieldtype": "Data",
			"width": 140,
		},
		{
			"fieldname": "orders_packed",
			"label": _("Orders Packed"),
			"fieldtype": "Int",
			"width": 130,
		},
		{
			"fieldname": "avg_weight",
			"label": _("Avg Weight"),
			"fieldtype": "Float",
			"width": 120,
		},
		{"fieldname": "hours", "label": _("Hours"), "fieldtype": "Float", "width": 90},
		{
			"fieldname": "orders_packed_per_hour",
			"label": _("Orders Packed / Hour"),
			"fieldtype": "Float",
			"width": 170,
		},
	]
