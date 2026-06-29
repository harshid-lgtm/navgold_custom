# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

"""Drop the child-table snapshot model now that Amazon Pricing Snapshot (the JSON one) is the
single source of truth. The parent must go after the child so no orphan link check fires."""

import frappe


def execute():
	for doctype in ("Amazon Price Snapshot", "Amazon Price Snapshot Item"):
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=True, ignore_missing=True)
