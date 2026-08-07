# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe

# The mobile scan surface moved from an in-form section to a floating button, and
# sync_customizations only inserts/updates, so these fields have to be removed here.
OBSOLETE_FIELDS = (
	"custom_picking_section",
	"custom_scan_tote",
	"custom_scan_item",
	"custom_details_section",
)


def execute():
	for fieldname in OBSOLETE_FIELDS:
		name = frappe.db.get_value("Custom Field", {"dt": "Pick List", "fieldname": fieldname})
		if name:
			frappe.delete_doc("Custom Field", name, ignore_permissions=True)
