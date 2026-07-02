# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe


def execute():
	# Cost Sub Type has been folded into the Cost Type tree (its records are shadowed as leaf Cost
	# Types by the preceding migrate patch). Remove the now-dead doctype and its table.
	if frappe.db.exists("DocType", "Cost Sub Type"):
		frappe.delete_doc("DocType", "Cost Sub Type", ignore_missing=True, force=True)

	# delete_doc drops the DocType record but leaves the physical table orphaned, so drop it here too.
	if frappe.db.table_exists("Cost Sub Type"):
		drop_cost_sub_type_table()


def drop_cost_sub_type_table():
	table = "tabCost Sub Type"
	if frappe.db.db_type == "postgres":
		frappe.db.sql_ddl(f'DROP TABLE IF EXISTS "{table}"')
	else:
		frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `{table}`")
