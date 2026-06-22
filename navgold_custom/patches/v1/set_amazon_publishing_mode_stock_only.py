import frappe


def execute():
	# Client decision: Amazon receives stock-only feeds until pricing data is re-verified.
	frappe.db.set_single_value("Navgold Settings", "amazon_publishing_mode", "Stock Only")
