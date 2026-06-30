import frappe


def execute():
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
