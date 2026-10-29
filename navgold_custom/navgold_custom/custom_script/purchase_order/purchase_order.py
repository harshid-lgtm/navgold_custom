import frappe
from frappe.utils.csvutils import to_csv


def _rank_sort_value(rank):
	"""Treat a positive supplier rank as preferred; unranked/blocked rows sort last."""
	rank = rank or 0
	return rank if rank > 0 else float("inf")


def get_supplier_part_number_value(supplier, item_code, uom):
	"""Return the supplier's part number for an exact supplier/item/UOM match."""
	if not (supplier and item_code and uom):
		return ""

	rows = frappe.get_all(
		"Supplier Item Price and Stock",
		filters={
			"supplier": supplier,
			"item": item_code,
			"uom": uom,
			"supplier_part_number": ["is", "set"],
		},
		fields=["name", "supplier_part_number", "rank", "modified"],
		order_by="modified desc",
	)

	if not rows:
		return ""

	best = min(
		rows,
		key=lambda row: (
			_rank_sort_value(row.rank),
			str(row.modified or ""),
			row.name,
		),
	)
	return best.supplier_part_number or ""


@frappe.whitelist()
def get_supplier_part_number(supplier: str, item_code: str, uom: str) -> str:
	return get_supplier_part_number_value(supplier, item_code, uom)


def set_supplier_part_no(doc, method=None):
	"""Populate ERPNext's standard Purchase Order Item supplier_part_no field."""
	for item in doc.items:
		item.supplier_part_no = get_supplier_part_number_value(
			doc.supplier,
			item.item_code,
			item.uom,
		)


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
