# Copyright (c) 2026, Navgold Ltd and contributors
# For license information, please see license.txt

import math
import re

import frappe
from frappe import _
from frappe.utils import cint
from frappe.utils.background_jobs import enqueue, is_job_enqueued
from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

from navgold_custom.navgold_custom.doctype.supplier_item_price_and_stock.supplier_item_price_and_stock import (
	update_supplier_rank,
)

SUMA_SUPPLIER = "Suma Wholefoods"
SUMA_STOCK_JOB_ID_PREFIX = "suma_stock_import::"

STOCK_HEADER_ALIASES = {"stock", "balance", "qty", "quantity", "stock no"}
PLCODE_HEADER_ALIASES = {"plcode", "pl code"}
BARCODE_HEADER_ALIASES = {"barcodeinner", "barcode inner", "barcode_inner"}


def normalize_header(value) -> str:
	return re.sub(r"\s+", " ", str(value or "").strip().lower())


def normalize_supplier_code(value):
	if value is None:
		return None
	s = str(value).strip()
	if not s or s.lower() == "nan":
		return None
	if re.fullmatch(r"\d+\.0", s):
		s = s[:-2]
	return s


def normalize_barcode(value):
	if value is None:
		return None
	s = str(value).strip()
	if not s or s.lower() == "nan":
		return None
	if re.fullmatch(r"\d+\.0", s):
		s = s[:-2]
	if re.search(r"[eE]", s):
		try:
			s = str(int(float(s)))
		except (TypeError, ValueError, OverflowError):
			pass
	return re.sub(r"\s+", "", s)


def floor_stock_qty_nonneg(value) -> int:
	if value is None:
		return 0
	s = str(value).strip().replace(",", "")
	if not s or s.lower() == "nan":
		return 0
	try:
		parsed = float(s)
		if math.isnan(parsed) or math.isinf(parsed):
			return 0
		return max(int(math.floor(parsed)), 0)
	except (TypeError, ValueError):
		return 0


def apply_suma_stock_buffer(stock_qty: int, buffer_qty: int) -> int:
	stock_qty = max(cint(stock_qty), 0)
	buffer_qty = max(cint(buffer_qty), 0)
	if buffer_qty > 0 and stock_qty < buffer_qty:
		return 0
	return stock_qty


def detect_columns(headers):
	normalized = [normalize_header(header) for header in headers]
	column_map = {}

	for idx, header in enumerate(normalized):
		if header in PLCODE_HEADER_ALIASES and "plcode" not in column_map:
			column_map["plcode"] = idx
		elif header in BARCODE_HEADER_ALIASES and "barcode" not in column_map:
			column_map["barcode"] = idx
		elif header in STOCK_HEADER_ALIASES and "stock" not in column_map:
			column_map["stock"] = idx

	missing = [name for name in ("plcode", "barcode", "stock") if name not in column_map]
	if missing:
		stock_found = column_map.get("stock") is not None
		stock_help = ""
		if not stock_found:
			stock_help = " " + _("Recognised stock headers are: {0}.").format(
				", ".join(sorted(STOCK_HEADER_ALIASES))
			)

		frappe.throw(
			_("Suma raw file format is not recognised. Missing: {0}. Columns found: {1}.{2}").format(
				", ".join(missing),
				", ".join(str(header or "") for header in headers),
				stock_help,
			)
		)

	return column_map


def build_raw_indexes(rows, column_map):
	by_supplier_code = {}
	by_barcode = {}
	duplicate_supplier_codes = set()
	duplicate_barcodes = set()

	for row in rows:
		if not row or not any(value not in (None, "") for value in row):
			continue

		supplier_code = normalize_supplier_code(
			row[column_map["plcode"]] if len(row) > column_map["plcode"] else None
		)
		barcode = normalize_barcode(row[column_map["barcode"]] if len(row) > column_map["barcode"] else None)
		stock = floor_stock_qty_nonneg(row[column_map["stock"]] if len(row) > column_map["stock"] else None)

		payload = {"supplier_code": supplier_code, "barcode": barcode, "stock": stock}

		if supplier_code:
			if supplier_code in by_supplier_code:
				duplicate_supplier_codes.add(supplier_code)
			else:
				by_supplier_code[supplier_code] = payload

		if barcode:
			if barcode in by_barcode:
				duplicate_barcodes.add(barcode)
			else:
				by_barcode[barcode] = payload

	return by_supplier_code, by_barcode, duplicate_supplier_codes, duplicate_barcodes


@frappe.whitelist(methods=["POST"])
def start_suma_stock_import(file_url: str):
	frappe.only_for("System Manager")

	if not file_url:
		frappe.throw(_("Please upload the raw Suma Excel file."))

	file_doc = frappe.get_doc("File", {"file_url": file_url})
	extension = file_doc.get_extension()[1].lstrip(".").lower()
	if extension not in ("xlsx", "xls"):
		frappe.throw(_("Suma stock import only supports Excel files (.xlsx or .xls)."))

	doc = frappe.get_doc(
		{
			"doctype": "Supplier Stock Import",
			"supplier": SUMA_SUPPLIER,
			"currency": frappe.db.get_value("Supplier", SUMA_SUPPLIER, "default_currency")
			or frappe.defaults.get_global_default("currency"),
			"entry_list": file_url,
			"use_supplier_part_number": 1,
			"import_stock": 1,
			"import_price": 0,
			"status": "In Progress",
		}
	).insert(ignore_permissions=True)

	file_doc.db_set(
		{
			"attached_to_doctype": doc.doctype,
			"attached_to_name": doc.name,
		},
		update_modified=False,
	)

	job_id = f"{SUMA_STOCK_JOB_ID_PREFIX}{doc.name}"
	if is_job_enqueued(job_id):
		frappe.throw(_("This Suma stock import is already in progress."))

	enqueue(
		run_suma_stock_import,
		queue="long",
		job_id=job_id,
		docname=doc.name,
		user=frappe.session.user,
		now=frappe.conf.developer_mode,
	)

	return {"docname": doc.name}


def run_suma_stock_import(docname: str, user: str | None = None):
	doc = frappe.get_doc("Supplier Stock Import", docname)

	try:
		file_doc = frappe.get_doc("File", {"file_url": doc.entry_list})
		data = read_xlsx_file_from_attached_file(fcontent=file_doc.get_content())
		if not data or len(data) < 2:
			frappe.throw(_("No data found in the uploaded Suma file."))

		column_map = detect_columns(data[0])
		(
			by_supplier_code,
			by_barcode,
			duplicate_supplier_codes,
			duplicate_barcodes,
		) = build_raw_indexes(data[1:], column_map)

		records = frappe.get_all(
			"Supplier Item Price and Stock",
			filters={"supplier": SUMA_SUPPLIER},
			fields=["name", "supplier_part_number", "barcode", "stock_qty"],
		)

		if not records:
			frappe.throw(_("No Supplier Item Price and Stock records found for {0}.").format(SUMA_SUPPLIER))

		buffer_qty = cint(frappe.db.get_single_value("Navgold Settings", "suma_stock_buffer"))
		matched_supplier_code = 0
		matched_barcode = 0
		unmatched = 0
		buffered_to_zero = 0
		changed = 0

		doc.total_rows = len(records)
		doc.successful_imports = 0
		doc.failed_imports = 0
		doc.import_log = ""

		for index, record in enumerate(records, start=1):
			supplier_code = normalize_supplier_code(record.supplier_part_number)
			barcode = normalize_barcode(record.barcode)

			raw = by_supplier_code.get(supplier_code) if supplier_code else None
			if raw is not None:
				matched_supplier_code += 1
			else:
				raw = by_barcode.get(barcode) if barcode else None
				if raw is not None:
					matched_barcode += 1

			if raw is None:
				new_stock = 0
				unmatched += 1
			else:
				raw_stock = raw["stock"]
				new_stock = apply_suma_stock_buffer(raw_stock, buffer_qty)
				if raw_stock > 0 and new_stock == 0:
					buffered_to_zero += 1

			if cint(record.stock_qty) != new_stock:
				record_doc = frappe.get_doc("Supplier Item Price and Stock", record.name)
				record_doc.stock_qty = new_stock
				record_doc.flags.bulk_entry = True
				record_doc.save(ignore_permissions=True)
				changed += 1

			doc.successful_imports += 1

			if index % 100 == 0:
				frappe.db.commit()  # nosemgrep: frappe-manual-commit
				frappe.publish_realtime(
					"suma_stock_import_progress",
					{"docname": doc.name, "current": index, "total": len(records)},
					user=user,
				)

		doc.status = "Success"
		doc.append_log(
			"\n".join(
				[
					f"Suma stock import completed with buffer {buffer_qty}.",
					f"ERP supplier records checked: {len(records)}",
					f"Matched by Supplier Code: {matched_supplier_code}",
					f"Matched by Barcode fallback: {matched_barcode}",
					f"Unmatched ERP records set to 0: {unmatched}",
					f"Matched rows below buffer set to 0: {buffered_to_zero}",
					f"Supplier stock records changed: {changed}",
					f"Duplicate raw PLCODE values ignored after first row: {len(duplicate_supplier_codes)}",
					f"Duplicate raw BarcodeINNER values ignored after first row: {len(duplicate_barcodes)}",
				]
			)
		)
		doc.save(ignore_permissions=True)
		update_supplier_rank()

	except Exception as error:
		doc.status = "Error"
		doc.failed_imports = doc.total_rows or 0
		doc.append_log(f"Suma stock import failed before completion: {error!s}")
		doc.save(ignore_permissions=True)
		frappe.log_error(title="Suma Stock Import Error", message=frappe.get_traceback())
		frappe.publish_realtime(
			"suma_stock_imported",
			{"docname": doc.name, "error": str(error)},
			user=user,
		)
		return

	frappe.publish_realtime(
		"suma_stock_imported",
		{"docname": doc.name},
		user=user,
	)
