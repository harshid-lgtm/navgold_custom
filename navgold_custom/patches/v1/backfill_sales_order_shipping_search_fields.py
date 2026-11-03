import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from navgold_custom.setup import get_sales_order_custom_fields

SHIPPING_SEARCH_FIELDS = {
	"shipping_address_line_1",
	"shipping_city",
	"shipping_postcode",
}


def ensure_shipping_search_fields():
	"""Create the searchable shipping fields before this post-model-sync backfill uses them."""
	definitions = get_sales_order_custom_fields()["Sales Order"]
	create_custom_fields(
		{"Sales Order": [field for field in definitions if field.get("fieldname") in SHIPPING_SEARCH_FIELDS]},
		ignore_validate=True,
	)


def execute():
	ensure_shipping_search_fields()

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
