import frappe


def execute():
	# pre_model_sync: read the booleans while they still exist, reload to create calculation_type,
	# then backfill before post_model_sync drops the old columns.
	if not frappe.db.has_column("Cost Type", "is_vat"):
		return

	resolved = {}
	for row in frappe.get_all("Cost Type", fields=["name", "is_absolute", "is_relative", "is_vat"]):
		if row.is_vat:
			resolved[row.name] = "VAT"
		elif row.is_relative:
			resolved[row.name] = "Relative"
		else:
			resolved[row.name] = "Absolute"

	frappe.reload_doc("navgold_custom", "doctype", "cost_type")

	for name, calculation_type in resolved.items():
		frappe.db.set_value("Cost Type", name, "calculation_type", calculation_type)
