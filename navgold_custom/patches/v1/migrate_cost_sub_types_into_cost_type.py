# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.utils.nestedset import rebuild_tree

# The source policy for each fed sub-type (mirrors the hardcoded fan-out in Bulk Reset Purchase Cost
# Source). Resolved here at migrate time — the Cost Sub Type doctype carries no `source` column on a
# clean production DB (its dev-only field never shipped, and this PR deletes the doctype), so we derive
# the policy from the sub-type name/cost-type rather than reading a column that may not exist.
SUB_TYPE_TO_AUTO_SOURCE = {
	"Purchase Cost-Warehouse Cost": "PO-Sync",
	"Purchase Cost-Top Supplier": "Supplier-Sync",
	"Purchase Cost-Last Transfer": "FBA-Transfer",
	"Fulfillment Cost-Estimated FBA Fee": "Amazon-Sync",
	"Fulfillment Cost-Domestic Regular": "Shipping-Sync",
	"Fulfillment Cost-Amazon Shipping": "Shipping-Sync",
}


def execute():
	# The Cost Sub Type doctype is dropped in a later patch; a re-run (or fresh branch) must no-op
	# rather than query a missing table.
	if not frappe.db.table_exists("Cost Sub Type"):
		return

	vat_cost_types = set(frappe.get_all("Cost Type", filters={"calculation_type": "VAT"}, pluck="name"))

	# Shadow every Cost Sub Type as a leaf Cost Type with the same name so existing
	# Amazon Product Cost.cost_sub_type links and future readers resolve against the tree.
	sub_types = frappe.get_all(
		"Cost Sub Type",
		fields=["name", "sub_type_name", "cost_type", "default_value"],
	)

	for sub_type in sub_types:
		if frappe.db.exists("Cost Type", sub_type.name):
			continue

		source, default_value = resolve_source_and_default(sub_type, vat_cost_types)

		# get_doc/insert is required: NestedSet must set lft/rgt on insert.
		frappe.get_doc(
			{
				"doctype": "Cost Type",
				"cost_type_name": sub_type.sub_type_name,
				"parent_cost_type": sub_type.cost_type,
				"is_group": 0,
				"source": source,
				"default_value": default_value,
			}
		).insert(ignore_permissions=True)

	rebuild_tree("Cost Type")

	assert_leaf_count_matches_sub_types()
	assert_all_cost_row_links_resolve()


def resolve_source_and_default(sub_type, vat_cost_types):
	target_source = SUB_TYPE_TO_AUTO_SOURCE.get(sub_type.name)
	if not target_source and sub_type.cost_type in vat_cost_types:
		target_source = "Item-Tax-Sync"

	# Fed leaves are feed-owned: source set, default value cleared. Self-contained leaves keep their
	# original default value and carry no source.
	if target_source:
		return target_source, 0

	return "", sub_type.default_value


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
