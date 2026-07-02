# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.utils.nestedset import rebuild_tree


def execute():
	# The Cost Sub Type doctype is dropped in a later patch; a re-run (or fresh branch) must no-op
	# rather than query a missing table.
	if not frappe.db.table_exists("Cost Sub Type"):
		return

	# Shadow every Cost Sub Type as a leaf Cost Type with the same name so existing
	# Amazon Product Cost.cost_sub_type links and future readers resolve against the tree.
	sub_types = frappe.get_all(
		"Cost Sub Type",
		fields=["name", "sub_type_name", "cost_type", "source", "default_value"],
	)

	for sub_type in sub_types:
		if frappe.db.exists("Cost Type", sub_type.name):
			continue
		# get_doc/insert is required: NestedSet must set lft/rgt on insert.
		frappe.get_doc(
			{
				"doctype": "Cost Type",
				"cost_type_name": sub_type.sub_type_name,
				"parent_cost_type": sub_type.cost_type,
				"is_group": 0,
				"source": sub_type.source,
				"default_value": sub_type.default_value,
			}
		).insert(ignore_permissions=True)

	rebuild_tree("Cost Type")

	assert_leaf_count_matches_sub_types()
	assert_all_cost_row_links_resolve()


def assert_leaf_count_matches_sub_types():
	leaf_count = frappe.db.count("Cost Type", {"is_group": 0})
	sub_type_count = frappe.db.count("Cost Sub Type")
	if leaf_count != sub_type_count:
		frappe.throw(
			f"Cost Type leaf count ({leaf_count}) does not match Cost Sub Type count ({sub_type_count})."
		)


def assert_all_cost_row_links_resolve():
	linked_sub_types = frappe.get_all(
		"Amazon Product Cost",
		filters={"cost_sub_type": ["is", "set"]},
		pluck="cost_sub_type",
		distinct=True,
	)
	existing = set(frappe.get_all("Cost Type", pluck="name"))
	unresolved = [name for name in linked_sub_types if name not in existing]
	if unresolved:
		frappe.throw(f"Amazon Product Cost links with no Cost Type record: {unresolved}")
