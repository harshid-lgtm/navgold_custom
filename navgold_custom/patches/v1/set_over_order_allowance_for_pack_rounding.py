# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt

# A supplier ships whole packs, so a Purchase Order raised from To Purchase always rounds the
# Material Request's loose-unit demand up to the next pack. That rounding reads as an over-order,
# and with no allowance ERPNext rejects the whole batch. The overshoot is always under one pack but
# unbounded as a percentage — needing 2 units from a Pack of 6 supplier is already 200% — so the
# allowance has to be large enough to never bind rather than tuned to a realistic overshoot.
ALLOWANCE_PERCENT = 10000


def execute():
	current_allowance = flt(frappe.db.get_single_value("Buying Settings", "over_order_allowance"))
	if current_allowance >= ALLOWANCE_PERCENT:
		return

	frappe.db.set_single_value("Buying Settings", "over_order_allowance", ALLOWANCE_PERCENT)
	print(f"Raised Buying Settings over_order_allowance from {current_allowance} to {ALLOWANCE_PERCENT}.")
