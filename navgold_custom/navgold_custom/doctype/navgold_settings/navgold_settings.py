# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

BATCH_SIZE = 5000


class NavgoldSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		buying_price_list: DF.Link
		tote_weight_limit: DF.Int
	# end: auto-generated types

	pass

	@frappe.whitelist()
	def generate_proxy_skus_for_items(self):
		total = frappe.db.count("UOM Conversion Detail", filters={"parenttype": "Item"})

		batch = BATCH_SIZE
		for start in range(0, total, batch):
			frappe.enqueue(
				"navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings.generate_proxy_skus_batch",
				start=start,
				batch=batch,
				queue="long",
			)


def generate_proxy_skus_batch(start, batch):
	uoms = frappe.get_all(
		"UOM Conversion Detail",
		filters={"parenttype": "Item"},
		fields=["parent", "uom"],
		limit_start=start,
		limit_page_length=batch,
	)
	for uom in uoms:
		try:
			if not frappe.db.exists("Proxy SKU", {"item": uom.parent, "uom": uom.uom}):
				doc = frappe.new_doc("Proxy SKU")
				doc.item = uom.parent
				doc.uom = uom.uom
				doc.insert(ignore_permissions=True)
		except Exception as e:
			frappe.log_error(f"Error creating Proxy SKU for {uom.parent} - {uom.uom}", str(e))
	frappe.db.commit()
