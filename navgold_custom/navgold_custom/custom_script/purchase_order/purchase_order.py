import frappe
from frappe.utils.csvutils import to_csv


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


@frappe.whitelist()
def export_po_items_summary(purchase_order: str):
	doc = frappe.get_doc("Purchase Order", purchase_order)
	doc.check_permission("read")

	summary = {}
	for item in doc.items:
		key = item.item_code
		if key not in summary:
			summary[key] = {
				"item_code": item.item_code,
				"description": item.item_name,
				"qty": 0,
				"uom": item.uom,
				"supplier_part_no": item.supplier_part_no or "",
				"rate": item.rate,
			}
		summary[key]["qty"] += item.qty

	headers = ["Item Code", "Description", "Quantity", "UOM", "Supplier Part Number", "Rate (GBP)"]
	data = [headers]
	for row in summary.values():
		data.append(
			[
				row["item_code"],
				row["description"],
				row["qty"],
				row["uom"],
				row["supplier_part_no"],
				row["rate"],
			]
		)

	supplier_name = doc.supplier_name or doc.supplier or purchase_order
	filename = f"{supplier_name}.csv"

	frappe.response["filename"] = filename
	frappe.response["filecontent"] = to_csv(data)
	frappe.response["type"] = "binary"
