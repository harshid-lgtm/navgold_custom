from frappe import _


def get_data(data):
	data.setdefault("transactions", []).append(
		{"label": _("Supplier"), "items": ["Supplier Item Price and Stock"]}
	)

	return data
