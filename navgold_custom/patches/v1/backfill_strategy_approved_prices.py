import frappe


def execute():
	frappe.db.sql(
		"""
		UPDATE `tabAmazon Product Strategy`
		SET
			approved_min_price = calculated_min_price,
			approved_max_price = calculated_max_price,
			approval_status = 'Approved',
			approval_requested_at = NULL
		"""
	)
