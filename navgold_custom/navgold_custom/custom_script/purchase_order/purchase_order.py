import frappe


def set_supplier_part_no(doc, method):
	if not doc.supplier:
		return

	item_codes = [item.item_code for item in doc.items if item.item_code]
	if not item_codes:
		return

	part_nos = frappe._dict(
		frappe.get_all(
			"Supplier Item Price and Stock",
			filters={"supplier": doc.supplier, "item": ("in", item_codes)},
			fields=["item", "supplier_part_number"],
			as_list=True,
		)
	)

	for item in doc.items:
		if item.item_code and part_nos.get(item.item_code):
			item.supplier_part_no = part_nos[item.item_code]
