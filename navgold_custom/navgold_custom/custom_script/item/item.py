import frappe


def set_default_uom_conversion_factor(doc, method):
	for uom in doc.uoms:
		if not uom.conversion_factor:
			uom.conversion_factor = frappe.db.get_value("UOM", uom.uom, "custom_default_conversion_factor")
