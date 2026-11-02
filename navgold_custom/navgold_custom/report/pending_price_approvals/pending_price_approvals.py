# Copyright (c) 2026, Navgold and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.data import flt


def execute(filters: dict | None = None):
	return get_columns(), get_data(filters or [])


def get_columns() -> list[dict]:
	return [
		{
			"label": _("Proxy SKU"),
			"fieldname": "proxy_sku",
			"fieldtype": "Link",
			"options": "Proxy SKU",
			"width": 180,
		},
		{
			"label": _("Strategy"),
			"fieldname": "strategy",
			"fieldtype": "Link",
			"options": "Amazon Product Strategy",
			"width": 260,
		},
		{
			"label": _("Strategy Type"),
			"fieldname": "strategy_name",
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"label": _("Current Active"),
			"fieldname": "is_current_active",
			"fieldtype": "Check",
			"width": 110,
		},
		{
			"label": _("Approved Min"),
			"fieldname": "approved_min_price",
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"label": _("Proposed Min"),
			"fieldname": "calculated_min_price",
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"label": _("Min Change"),
			"fieldname": "min_change",
			"fieldtype": "Currency",
			"width": 110,
		},
		{
			"label": _("Approved Max"),
			"fieldname": "approved_max_price",
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"label": _("Proposed Max"),
			"fieldname": "calculated_max_price",
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"label": _("Max Change"),
			"fieldname": "max_change",
			"fieldtype": "Currency",
			"width": 110,
		},
		{
			"label": _("Max Change %"),
			"fieldname": "max_change_percent",
			"fieldtype": "Percent",
			"width": 120,
		},
		{
			"label": _("Min Margin %"),
			"fieldname": "min_margin",
			"fieldtype": "Percent",
			"width": 110,
		},
		{
			"label": _("Max Margin %"),
			"fieldname": "max_margin",
			"fieldtype": "Percent",
			"width": 110,
		},
		{
			"label": _("Requested At"),
			"fieldname": "approval_requested_at",
			"fieldtype": "Datetime",
			"width": 160,
		},
	]


def get_data(filters: dict) -> list[dict]:
	rows = frappe.db.sql(
		"""
		SELECT
			aps.name AS strategy,
			aps.proxy_sku,
			aps.strategy_name,
			aps.approved_min_price,
			aps.approved_max_price,
			aps.calculated_min_price,
			aps.calculated_max_price,
			aps.min_margin,
			aps.max_margin,
			aps.approval_requested_at,
			CASE WHEN app.active_strategy = aps.name THEN 1 ELSE 0 END AS is_current_active
		FROM `tabAmazon Product Strategy` aps
		LEFT JOIN `tabAmazon Product Pricing` app
			ON app.proxy_sku = aps.proxy_sku
		WHERE aps.approval_status = 'Pending Approval'
		ORDER BY is_current_active DESC, aps.approval_requested_at ASC, aps.proxy_sku ASC
		""",
		as_dict=True,
	)

	for row in rows:
		row["min_change"] = flt(row.calculated_min_price, 2) - flt(row.approved_min_price, 2)
		row["max_change"] = flt(row.calculated_max_price, 2) - flt(row.approved_max_price, 2)
		approved_max = flt(row.approved_max_price, 2)
		row["max_change_percent"] = flt((row["max_change"] / approved_max) * 100, 2) if approved_max else 0

	return rows


@frappe.whitelist(methods=["POST"])
def approve_price_changes(strategy_names: str | list | None = None, approve_all: int = 0) -> dict:
	frappe.only_for("System Manager")

	if approve_all:
		names = frappe.get_all(
			"Amazon Product Strategy",
			filters={"approval_status": "Pending Approval"},
			pluck="name",
		)
	else:
		if isinstance(strategy_names, str):
			strategy_names = frappe.parse_json(strategy_names)
		names = list(dict.fromkeys(strategy_names or []))

	if not names:
		frappe.throw(_("No pending price approvals selected."))

	approved = 0
	skipped = []

	for strategy_name in names:
		try:
			strategy = frappe.get_doc("Amazon Product Strategy", strategy_name)
			if strategy.approval_status != "Pending Approval":
				skipped.append({"strategy": strategy_name, "reason": _("No longer pending approval")})
				continue

			strategy.approve_proposed_price()
			approved += 1
		except Exception as exception:
			skipped.append({"strategy": strategy_name, "reason": str(exception)})

	return {"approved": approved, "skipped": skipped}
