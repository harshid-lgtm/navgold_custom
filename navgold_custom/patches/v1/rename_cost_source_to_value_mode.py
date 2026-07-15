# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
	# Guard on real DB columns, not meta: at pre_model_sync the meta is already loaded from the new
	# JSON (which has value_mode), so a meta-based check would wrongly skip the rename.
	columns = frappe.db.get_table_columns("Amazon Product Cost")

	if "source" in columns and "value_mode" not in columns:
		# Clean case: rename the column in place, carrying all data under the new name.
		rename_field("Amazon Product Cost", "source", "value_mode")
	elif "source" in columns and "value_mode" in columns:
		# Recovery: model sync already added an empty value_mode beside the orphan source column.
		# Copy the real values across, then drop the orphan so it can't drift.
		cost = frappe.qb.DocType("Amazon Product Cost")
		frappe.qb.update(cost).set(cost.value_mode, cost.source).run()
		frappe.db.sql_ddl("ALTER TABLE `tabAmazon Product Cost` DROP COLUMN `source`")
