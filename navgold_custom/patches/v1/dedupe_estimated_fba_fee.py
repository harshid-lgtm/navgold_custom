import frappe

SUB_TYPE = "Fulfillment Cost-Estimated FBA Fee"


def execute():
	duplicates = frappe.db.sql(
		"""SELECT parent
		   FROM `tabAmazon Product Cost`
		   WHERE cost_sub_type = %s
		   GROUP BY parent
		   HAVING COUNT(*) > 1""",
		(SUB_TYPE,),
		as_dict=True,
	)
	if not duplicates:
		return

	duplicate_parents = [row["parent"] for row in duplicates]

	all_rows = frappe.get_all(
		"Amazon Product Cost",
		filters={"cost_sub_type": SUB_TYPE, "parent": ["in", duplicate_parents]},
		fields=["name", "parent", "creation"],
		order_by="parent asc, creation asc",
	)

	to_delete = []
	seen_parents = set()
	for row in all_rows:
		if row["parent"] in seen_parents:
			to_delete.append(row["name"])
		else:
			seen_parents.add(row["parent"])

	if to_delete:
		batch_size = 1000
		for index in range(0, len(to_delete), batch_size):
			batch = to_delete[index : index + batch_size]
			frappe.db.delete("Amazon Product Cost", {"name": ["in", batch]})

	frappe.db.commit()  # nosemgrep: frappe-manual-commit -- patch, persist dedup before downstream

	print(
		f"Deduped {len(to_delete)} extra '{SUB_TYPE}' rows across "
		f"{len(duplicate_parents)} strategies (kept earliest per parent)"
	)
