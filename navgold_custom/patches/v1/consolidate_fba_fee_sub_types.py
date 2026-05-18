import frappe

OLD_SUB_TYPE = "Fulfillment Cost-FBA Fee"
NEW_SUB_TYPE = "Fulfillment Cost-Estimated FBA Fee"


def execute():
	if not frappe.db.exists("Cost Sub Type", OLD_SUB_TYPE):
		return

	old_rows = frappe.get_all(
		"Amazon Product Cost",
		filters={"cost_sub_type": OLD_SUB_TYPE},
		fields=["name", "parent", "parenttype", "value"],
	)
	if not old_rows:
		frappe.delete_doc("Cost Sub Type", OLD_SUB_TYPE, ignore_permissions=True, delete_permanently=True)
		return

	parents_with_old = {row["parent"] for row in old_rows}
	parents_with_new = set(
		frappe.get_all(
			"Amazon Product Cost",
			filters={
				"cost_sub_type": NEW_SUB_TYPE,
				"parent": ["in", list(parents_with_old)],
			},
			pluck="parent",
		)
	)

	to_delete = []
	to_rename = []
	for row in old_rows:
		if row["parent"] in parents_with_new:
			to_delete.append(row["name"])
		else:
			to_rename.append(row["name"])

	if to_rename:
		frappe.db.set_value(
			"Amazon Product Cost",
			{"name": ["in", to_rename]},
			"cost_sub_type",
			NEW_SUB_TYPE,
			update_modified=False,
		)

	if to_delete:
		frappe.db.delete("Amazon Product Cost", {"name": ["in", to_delete]})

	frappe.db.sql(
		"""UPDATE `tabAmazon Product Cost`
		   SET rank = 1, is_active = 1
		   WHERE cost_sub_type = %s AND (rank != 1 OR is_active != 1)""",
		(NEW_SUB_TYPE,),
	)

	template_rows = frappe.get_all(
		"Amazon Cost Set Template Item",
		filters={"cost_sub_type": OLD_SUB_TYPE},
		fields=["name", "parent"],
	)
	template_parents_with_old = {row["parent"] for row in template_rows}
	if template_parents_with_old:
		template_parents_with_new = set(
			frappe.get_all(
				"Amazon Cost Set Template Item",
				filters={
					"cost_sub_type": NEW_SUB_TYPE,
					"parent": ["in", list(template_parents_with_old)],
				},
				pluck="parent",
			)
		)
		tpl_to_delete = [r["name"] for r in template_rows if r["parent"] in template_parents_with_new]
		tpl_to_rename = [r["name"] for r in template_rows if r["parent"] not in template_parents_with_new]

		if tpl_to_rename:
			frappe.db.set_value(
				"Amazon Cost Set Template Item",
				{"name": ["in", tpl_to_rename]},
				"cost_sub_type",
				NEW_SUB_TYPE,
				update_modified=False,
			)
		if tpl_to_delete:
			frappe.db.delete("Amazon Cost Set Template Item", {"name": ["in", tpl_to_delete]})

	frappe.delete_doc("Cost Sub Type", OLD_SUB_TYPE, ignore_permissions=True, delete_permanently=True)

	frappe.db.commit()  # nosemgrep: frappe-manual-commit -- patch, persist consolidation before downstream patches run

	print(
		f"FBA Fee consolidation: renamed {len(to_rename)} rows to '{NEW_SUB_TYPE}', "
		f"deleted {len(to_delete)} duplicate rows, forced rank=1 on '{NEW_SUB_TYPE}' rows, "
		f"removed '{OLD_SUB_TYPE}' Cost Sub Type"
	)
