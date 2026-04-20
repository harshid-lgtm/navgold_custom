# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.core.doctype.data_import.importer import get_df_for_column_header
from frappe.model.document import Document
from frappe.utils.csvutils import read_csv_content


class POCSVExportTemplate(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		description: DF.SmallText | None
		mapping_preview: DF.HTML | None
		supplier: DF.Link
		template_file: DF.Attach
	# end: auto-generated types

	def get_template_headers(self):
		if not self.template_file:
			return []
		content = frappe.get_doc("File", {"file_url": self.template_file}).get_content()
		rows = read_csv_content(content)
		return rows[0] if rows else []

	def get_resolved_headers(self):
		return [
			(header, get_df_for_column_header("Purchase Order Item", header))
			for header in self.get_template_headers()
		]

	@frappe.whitelist()
	def get_field_mapping_preview(self):
		columns = [
			{
				"header_title": header,
				"df": df.as_dict() if df else None,
				"skip_import": df is None,
			}
			for header, df in self.get_resolved_headers()
		]
		return {"columns": columns, "data": [], "warnings": [], "max_rows_exceeded": False}
