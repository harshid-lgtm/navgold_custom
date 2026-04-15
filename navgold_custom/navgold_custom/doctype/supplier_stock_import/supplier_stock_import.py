# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.background_jobs import enqueue, is_job_enqueued
from frappe.utils.caching import redis_cache
from frappe.utils.csvutils import build_csv_response, get_csv_content_from_google_sheets, read_csv_content
from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

from navgold_custom.navgold_custom.doctype.supplier_item_price_and_stock.supplier_item_price_and_stock import (
	update_supplier_rank,
)

TEMPLATE_COLUMNS = ("Supplier Part Number", "Stock Qty", "UOM", "Price", "Barcode")


class SupplierStockImport(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		currency: DF.Link
		entry_list: DF.Attach | None
		errored_rows: DF.LongText | None
		failed_imports: DF.Int
		google_sheets_url: DF.Data | None
		import_log: DF.LongText | None
		import_price: DF.Check
		import_stock: DF.Check
		status: DF.Literal["Pending", "In Progress", "Success", "Partial Success", "Error"]
		successful_imports: DF.Int
		supplier: DF.Link
		total_rows: DF.Int
		use_supplier_part_number: DF.Check
	# end: auto-generated types

	def validate(self):
		if not self.currency:
			self.currency = frappe.defaults.get_global_default("currency")

		if not self.currency:
			frappe.throw(_("Please set a default currency in System Settings"))

	@frappe.whitelist()
	def start_import(self):
		if not (self.entry_list or self.google_sheets_url):
			frappe.throw(_("Please provide an import file or Google Sheets URL"))

		job_id = f"supplier_stock_import::{self.name}"

		if is_job_enqueued(job_id):
			frappe.throw(_("Import is already in progress"))

		self.status = "In Progress"
		self.save()

		enqueue(
			run_import,
			queue="long",
			job_id=job_id,
			docname=self.name,
			now=frappe.conf.developer_mode,
		)

	def process_import(self):
		try:
			self.status = "In Progress"
			data = self.get_file_data()

			if not data or len(data) < 2:
				frappe.throw(_("No data found in the uploaded file"))

			self.validate_template_columns(data[0])
			self.import_rows(data[1:])

		except Exception as e:
			self.status = "Error"
			self.append_log(f"Import failed: {e!s}")
			frappe.log_error(title="Supplier Stock Import Error", message=str(e))

	def get_file_data(self):
		if self.google_sheets_url:
			content = get_csv_content_from_google_sheets(self.google_sheets_url)
			return read_csv_content(content)

		if self.entry_list:
			file_doc = frappe.get_cached_doc("File", {"file_url": self.entry_list})
			extension = file_doc.get_extension()[1].lstrip(".").lower()
			if extension == "csv":
				content = file_doc.get_content()
				return read_csv_content(content)
			elif extension in ("xlsx", "xls"):
				content = file_doc.get_content()
				return read_xlsx_file_from_attached_file(fcontent=content)
			else:
				frappe.throw(_("Only CSV and Excel files are supported"))

		frappe.throw(_("Please provide an import file or Google Sheets URL"))

	def validate_template_columns(self, headers):
		headers_lower = [h.lower().strip() if h else "" for h in headers]

		for col in TEMPLATE_COLUMNS:
			if col.lower() not in headers_lower:
				frappe.throw(
					_("Missing required column: {0}. Required columns are: {1}").format(
						col, ", ".join(TEMPLATE_COLUMNS)
					)
				)

	def import_rows(self, rows):
		self.total_rows = len(rows)
		self.successful_imports = 0
		self.failed_imports = 0
		self.import_log = ""
		errored_rows_data = []

		for row_idx, row in enumerate(rows, start=2):
			try:
				self.process_row(row, row_idx)
				self.successful_imports += 1
			except Exception as e:
				self.failed_imports += 1
				self.append_log(f"Row {row_idx-1}: {e!s}")
				errored_rows_data.append([*list(row), str(e)])

			if self.total_rows > 5:
				frappe.publish_realtime(
					"supplier_import_progress",
					{
						"docname": self.name,
						"current": row_idx - 1,
						"total": self.total_rows,
					},
					user=frappe.session.user,
				)

			# Commit every 100 rows to avoid long-running transaction in background job
			if row_idx % 100 == 0:
				frappe.db.commit()  # nosemgrep: frappe-manual-commit

		self.errored_rows = frappe.as_json(errored_rows_data) if errored_rows_data else ""

		if self.failed_imports == 0:
			self.status = "Success"
		elif self.successful_imports > 0:
			self.status = "Partial Success"
		else:
			self.status = "Error"

		self.append_log(
			f"\nImport completed: {self.successful_imports} successful, {self.failed_imports} failed"
		)

	@redis_cache(ttl=7 * 24 * 60 * 60)
	def get_uom_from_barcode_and_conversion_factor(self, barcode, conversion_factor):
		item = self.get_item_from_barcode(barcode)
		if not item:
			frappe.throw(_("Item is required"))

		uom = frappe.db.get_value(
			"UOM Conversion Detail",
			{"parent": item, "conversion_factor": conversion_factor},
			"uom",
		)
		if uom:
			return uom
		# Fallback add uom to item
		uom = frappe.db.get_value(
			"UOM",
			{"custom_default_conversion_factor": conversion_factor},
			"name",
		)
		item_doc = frappe.get_cached_doc("Item", item)
		item_doc.append("uoms", {"uom": uom, "conversion_factor": conversion_factor}).save()
		return uom

	def process_row(self, row, row_idx):
		supplier_part_number = row[0] if len(row) > 0 else None
		stock_qty = frappe.cint(row[1]) if len(row) > 1 else 0
		uom = row[2] if len(row) > 2 else 0
		price = frappe.utils.flt(row[3]) if len(row) > 3 else 0
		barcode = row[4] if len(row) > 4 else None

		uom = self.get_uom_from_barcode_and_conversion_factor(barcode, uom)
		if self.use_supplier_part_number:
			if not supplier_part_number:
				raise ValueError(
					"Supplier Part Number is required when 'Use Supplier Part Number' is checked"
				)
		else:
			if not barcode:
				raise ValueError("Barcode is required")

		if not uom:
			raise ValueError("UOM is required")

		existing_record = self.get_existing_record(supplier_part_number, barcode, uom)

		if existing_record:
			self.update_record(existing_record, stock_qty, price, supplier_part_number)
			self.append_log(f"Row {row_idx-1}: Updated {existing_record}")
		else:
			item = self.get_item_from_barcode(barcode)
			if not item:
				raise ValueError(f"No item found for barcode: {barcode}")

			new_name = self.create_record(item, uom, stock_qty, price, supplier_part_number, barcode)
			self.append_log(f"Row {row_idx-1}: Created {new_name}")

	def get_existing_record(self, supplier_part_number, barcode, uom):
		if self.use_supplier_part_number:
			return frappe.db.get_value(
				"Supplier Item Price and Stock",
				{
					"supplier": self.supplier,
					"supplier_part_number": supplier_part_number,
					"uom": uom,
				},
				"name",
			)
		return frappe.db.get_value(
			"Supplier Item Price and Stock",
			{"supplier": self.supplier, "barcode": barcode, "uom": uom},
			"name",
		)

	def get_item_from_barcode(self, barcode):
		return frappe.db.get_value("Item Barcode", {"barcode": barcode}, "parent")

	# We need to run validate() to populate price therefore we use get_cached_doc and save instead of direct db calls
	def update_record(self, name, stock_qty, price, supplier_part_number):
		record = frappe.get_cached_doc("Supplier Item Price and Stock", name)
		if self.import_stock:
			record.stock_qty = stock_qty
		if self.import_price:
			record.price = price
		if supplier_part_number:
			record.supplier_part_number = supplier_part_number
		record.save(ignore_permissions=True)

	def create_record(self, item, uom, stock_qty, price, supplier_part_number, barcode):
		doc = frappe.get_doc(
			{
				"doctype": "Supplier Item Price and Stock",
				"item": item,
				"supplier": self.supplier,
				"uom": uom,
				"stock_qty": stock_qty,
				"price": price,
				"currency": self.currency,
				"supplier_part_number": supplier_part_number,
				"barcode": barcode,
				"purchase_blocked": 0,
			}
		)
		doc.flags.bulk_entry = True
		doc.insert(ignore_permissions=True)
		return doc.name

	def append_log(self, message):
		if self.import_log:
			self.import_log += "\n" + message
		else:
			self.import_log = message


@frappe.whitelist()
def export_errored_rows(docname: str):
	doc = frappe.get_cached_doc("Supplier Stock Import", docname)

	if not doc.errored_rows:
		frappe.throw(_("No errored rows to export"))

	errored_data = frappe.parse_json(doc.errored_rows)

	errored_data.insert(0, [*TEMPLATE_COLUMNS, "Error"])
	build_csv_response(errored_data, f"Errored Rows - {docname}.csv")


def run_import(docname):
	doc = frappe.get_cached_doc("Supplier Stock Import", docname)
	doc.process_import()
	doc.save()
	update_supplier_rank()
	frappe.publish_realtime("supplier_import_refresh", {"docname": docname}, user=frappe.session.user)
