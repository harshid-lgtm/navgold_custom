# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe

SETTINGS = "Navgold Settings"


def save_last_report_attachment(fieldname: str, file_name: str, content: str | bytes) -> str:
	previous = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": SETTINGS,
			"attached_to_name": SETTINGS,
			"attached_to_field": fieldname,
		},
		pluck="name",
	)
	if previous:
		frappe.db.set_single_value(SETTINGS, fieldname, None)
		for file_name_to_remove in previous:
			frappe.delete_doc("File", file_name_to_remove, ignore_permissions=True, delete_permanently=True)

	file_doc = frappe.new_doc("File")
	file_doc.file_name = file_name
	file_doc.content = content
	file_doc.is_private = 1
	file_doc.attached_to_doctype = SETTINGS
	file_doc.attached_to_name = SETTINGS
	file_doc.attached_to_field = fieldname
	file_doc.save(ignore_permissions=True)

	frappe.db.set_single_value(SETTINGS, fieldname, file_doc.file_url)
	return file_doc.file_url
