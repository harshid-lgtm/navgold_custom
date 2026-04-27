# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.background_jobs import enqueue, is_job_enqueued
from frappe.utils.csvutils import build_csv_response, get_csv_content_from_google_sheets, read_csv_content
from frappe.utils.data import cstr
from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file


class SupplierOfferImport(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		entry_list: DF.Attach | None
		errored_rows: DF.LongText | None
		failed_imports: DF.Int
		google_sheets_url: DF.Data | None
		import_log: DF.LongText | None
		status: DF.Literal["Pending", "In Progress", "Success", "Partial Success", "Error"]
		successful_imports: DF.Int
		supplier: DF.Link
		total_rows: DF.Int
	# end: auto-generated types

	@frappe.whitelist()
	def start_import(self):
		if not (self.entry_list or self.google_sheets_url):
			frappe.throw(_("Please provide an import file or Google Sheets URL"))

		job_id = f"supplier_offer_import::{self.name}"

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

			if not data[0] or len(data[0]) < 2:
				frappe.throw(_("File must have at least an Item column and one offer column"))

			self.import_rows(data[1:])

		except Exception as e:
			self.status = "Error"
			self.append_log(f"Import failed: {e!s}")
			frappe.log_error(title="Supplier Offer Import Error", message=str(e))

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

	def import_rows(self, rows):
		self.total_rows = len(rows)
		self.successful_imports = 0
		self.failed_imports = 0
		self.import_log = ""
		errored_rows_data = []

		for row_idx, row in enumerate(rows, start=2):
			try:
				updated_count = self.process_row(row)
				self.successful_imports += 1
				self.append_log(f"Row {row_idx-1}: Updated {updated_count} record(s) for item {row[0]}")
			except Exception as e:
				self.failed_imports += 1
				self.append_log(f"Row {row_idx-1}: {e!s}")
				errored_rows_data.append([*list(row), str(e)])

			if self.total_rows > 5:
				frappe.publish_realtime(
					"supplier_offer_import_progress",
					{
						"docname": self.name,
						"current": row_idx - 1,
						"total": self.total_rows,
					},
					user=frappe.session.user,
				)

			if row_idx % 100 == 0:
				frappe.db.commit()  # nosemgrep: frappe-manual-commit -- background job batch, persist partial progress

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

	def process_row(self, row):
		if not row or row[0] in (None, ""):
			raise ValueError("Item code is required")

		item_code = cstr(row[0]).strip()

		offer_values = []
		for cell in row[1:]:
			if cell in (None, ""):
				continue
			offer_values.append(cstr(cell).strip())

		if not offer_values:
			raise ValueError("No offer values found")

		matching_records = frappe.get_all(
			"Supplier Item Price and Stock",
			filters={"supplier": self.supplier, "item": item_code},
			pluck="name",
		)

		if not matching_records:
			raise ValueError(f"No Supplier Item Price and Stock found for item {item_code}")

		for record_name in matching_records:
			record = frappe.get_doc("Supplier Item Price and Stock", record_name)
			record.set("offers", [])
			for value in offer_values:
				record.append("offers", {"offer": value})
			record.flags.single_entry = False
			record.flags.bulk_entry = True
			record.save(ignore_permissions=True)

		return len(matching_records)

	def append_log(self, message):
		if self.import_log:
			self.import_log += "\n" + message
		else:
			self.import_log = message


@frappe.whitelist()
def export_errored_rows(docname: str):
	doc = frappe.get_cached_doc("Supplier Offer Import", docname)

	if not doc.errored_rows:
		frappe.throw(_("No errored rows to export"))

	errored_data = frappe.parse_json(doc.errored_rows)
	build_csv_response(errored_data, f"Errored Rows - {docname}")


def run_import(docname):
	doc = frappe.get_cached_doc("Supplier Offer Import", docname)
	doc.process_import()
	doc.save()
	frappe.publish_realtime("supplier_offer_import_refresh", {"docname": docname}, user=frappe.session.user)
