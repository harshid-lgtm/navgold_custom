import frappe


def execute():
	rows = frappe.get_all(
		"Sales Order",
		filters={"shipping_address_name": ["is", "set"]},
		fields=["name", "shipping_address_name"],
	)

	for row in rows:
		address = frappe.db.get_value(
			"Address",
			row.shipping_address_name,
			["address_line1", "city", "pincode"],
			as_dict=True,
		)
		if not address:
			continue

		frappe.db.set_value(
			"Sales Order",
			row.name,
			{
				"shipping_address_line_1": address.address_line1,
				"shipping_city": address.city,
				"shipping_postcode": address.pincode,
			},
			update_modified=False,
		)
