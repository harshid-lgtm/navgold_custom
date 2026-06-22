import frappe


def execute():
	# Client decision after the June 10 below-cost pricing incident: Amazon only receives
	# stock feeds until pricing data is re-verified; Repricer keeps receiving min/max.
	frappe.db.set_single_value("Navgold Settings", "amazon_publishing_mode", "Stock Only")
