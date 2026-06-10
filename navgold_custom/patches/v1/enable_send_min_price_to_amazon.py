import frappe


def execute():
	# Preserve prior behaviour: min price was always sent before this flag existed.
	frappe.db.set_single_value("Navgold Settings", "send_min_price_to_amazon", 1)
