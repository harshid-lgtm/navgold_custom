from frappe import _


def get_data(data):
	data.setdefault("non_standard_fieldnames", {})
	data["non_standard_fieldnames"]["Supplier Item Price and Stock"] = "item"

	data.setdefault("transactions", []).append(
		{"label": _("Supplier"), "items": ["Supplier Item Price and Stock"]}
	)

	data["non_standard_fieldnames"]["Proxy SKU"] = "item"

	data["transactions"].append({"label": _("Amazon Linked"), "items": ["Proxy SKU"]})

	return data
