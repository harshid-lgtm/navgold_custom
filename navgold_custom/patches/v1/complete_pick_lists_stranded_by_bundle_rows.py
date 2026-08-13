import frappe

from navgold_custom.navgold_custom.custom_script.delivery_note.delivery_note import (
	get_untracked_pick_lists,
)


def execute():
	"""Complete the Delivery pick lists left stranded before the fix.

	A bundle ships as one parent Delivery Note row, so only one of its component pick list rows was
	ever credited and the pick list could never leave Partly Delivered. Each one holds its tote, and
	the packing scan keys on the tote alone, so a stranded pick list leaks its stale draft lines into
	every later pick list packed in the same tote. Complete the ones whose Sales Orders are all
	settled; leave anything still owed alone."""
	stranded = get_untracked_pick_lists(
		frappe.get_all(
			"Pick List",
			filters={"purpose": "Delivery", "docstatus": 1, "status": ["in", ["Open", "Partly Delivered"]]},
			pluck="name",
		)
	)
	if not stranded:
		return

	sales_orders_by_pick_list = {}
	for row in frappe.get_all(
		"Pick List Item",
		filters={"parent": ["in", stranded], "docstatus": 1, "sales_order": ["is", "set"]},
		fields=["parent", "sales_order"],
	):
		sales_orders_by_pick_list.setdefault(row.parent, set()).add(row.sales_order)
	if not sales_orders_by_pick_list:
		return

	involved = set().union(*sales_orders_by_pick_list.values())
	delivery_status = {
		row.name: row.delivery_status
		for row in frappe.get_all(
			"Sales Order", filters={"name": ["in", list(involved)]}, fields=["name", "delivery_status"]
		)
	}

	completed = []
	for pick_list_name, sales_orders in sales_orders_by_pick_list.items():
		if not all(delivery_status.get(sales_order) == "Fully Delivered" for sales_order in sales_orders):
			continue

		frappe.db.set_value(
			"Pick List",
			pick_list_name,
			{"status": "Completed", "per_delivered": 100, "delivery_status": "Fully Delivered"},
		)
		completed.append(pick_list_name)

	if completed:
		freed_totes = frappe.get_all(
			"Pick List",
			filters={"name": ["in", completed], "custom_tote": ["is", "set"]},
			distinct=True,
			pluck="custom_tote",
		)
		print(
			f"Completed {len(completed)} pick lists whose Sales Orders are fully delivered, "
			f"freeing {len(freed_totes)} totes."
		)
