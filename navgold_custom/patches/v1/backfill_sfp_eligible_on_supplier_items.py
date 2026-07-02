import frappe


def execute():
	# Field is synced by sync_customizations, which runs after this patch; nothing to backfill yet.
	if not frappe.db.has_column("Supplier", "custom_sfp_eligible"):
		return

	eligible_suppliers = frappe.get_all(
		"Supplier",
		filters={"custom_sfp_eligible": 1},
		pluck="name",
	)
	if not eligible_suppliers:
		return

	frappe.db.set_value(
		"Supplier Item Price and Stock",
		{"supplier": ["in", eligible_suppliers], "sfp": 0},
		"sfp",
		1,
		update_modified=False,
	)
