# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class VendorOnboarding(Document):
	def on_submit(self):
		self.create_supplier_from_onboarding()

	def create_supplier_from_onboarding(self):
		# Avoid duplicates
		if frappe.db.exists("Supplier", {"supplier_name": self.supplier_name}):
			frappe.throw(f"Supplier '{self.supplier_name}' already exists.")

		# Create Supplier
		supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": self.supplier_name,
				"supplier_type": self.supplier_type,
				"custom_supplier_assigned_id": self.supplier_assigned_id,
				"tax_id": self.vat_number,
				"default_currency": self.currency,
				"country": self.country,
				"website": self.companys_website,
				"custom_sells_organic_products": self.sells_organic_products,
				"custom_organic_certificate": self.organic_certificate
				if self.sells_organic_products == "Yes"
				else None,
				"custom_organic_certificate_na_reason": self.organic_certificate_na_reason
				if self.sells_organic_products == "Not Applicable"
				else None,
				"custom_credit_limit": self.credit_limit,
				"custom_default_lead_time": self.default_lead_time,
				"custom_minimum_order_value": self.minimum_order_value,
				"supplier_details": self.other_details,
				"custom_onboarding_reference": self.name,
			}
		)
		supplier.insert()
		self.db_set("supplier", supplier.name)
		# Create Person Contact
		person_contact = None
		if self.contact_persons_email or self.contact_person or self.contact_persons_phone:
			person_contact = frappe.get_doc(
				{
					"doctype": "Contact",
					"first_name": self.contact_person,
					"phone_nos": [
						{
							"phone": self.contact_persons_phone,
							"is_primary_phone": 1,
							"is_primary_mobile_no": 1,
						}
					],
					"email_ids": [{"email_id": self.contact_persons_email, "is_primary": 1}],
					"is_primary_contact": 1,
					"links": [{"link_doctype": "Supplier", "link_name": supplier.name}],
				}
			)
			person_contact.insert()

		# Create Address
		address = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": f"{supplier.supplier_name} (Company Address)",
				"address_line1": self.address_1,
				"address_line2": self.address_2,
				"address_type": "Office",
				"city": self.citytown,
				"pincode": self.postal_code,
				"country": self.country,
				"email_id": self.companys_email,
				"phone": self.companys_phone,
				"is_primary_address": 1,
				"is_shipping_address": 1,
				"links": [{"link_doctype": "Supplier", "link_name": supplier.name}],
			}
		)
		address.insert()
		if not person_contact:
			frappe.msgprint(f"Supplier {supplier.name} created with Address:{address.name} successfully.")
		else:
			frappe.msgprint(
				f"Supplier {supplier.name} created with Contact: {person_contact.name} and Address:{address.name} successfully."
			)
