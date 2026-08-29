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
		if item.item_code not in summary:
			row = item.as_dict()
			row["qty"] = 0
			row["stock_qty"] = 0
			summary[item.item_code] = row
		summary[item.item_code]["qty"] += item.qty
		summary[item.item_code]["stock_qty"] += item.stock_qty

	template_name = frappe.db.get_value("Supplier", doc.supplier, "custom_csv_export_template")
	if template_name:
		template = frappe.get_doc("PO CSV Export Template", template_name)
		data = build_data_from_template(template, summary)
	else:
		data = build_default_data(summary)

	supplier_name = doc.supplier_name or doc.supplier or purchase_order
	frappe.response["filename"] = f"{supplier_name}.csv"
	frappe.response["filecontent"] = to_csv(data)
	frappe.response["type"] = "binary"


def build_default_data(summary):
	headers = [
		"Item Code",
		"Description",
		"Quantity in Nos",
		"UOM",
		"Supplier Part Number",
		"Rate (GBP)",
		"Quantity",
	]
	fields = ["item_code", "item_name", "stock_qty", "uom", "supplier_part_no", "rate", "qty"]
	rows = [[row.get(f) for f in fields] for row in summary.values()]
	return [headers, *rows]


def build_data_from_template(template, summary):
	resolved = template.get_resolved_headers()
	headers = [h for h, _ in resolved]
	fields = [df.fieldname if df else None for _, df in resolved]
	rows = [[row.get(f) if f else "" for f in fields] for row in summary.values()]
	return [headers, *rows]
