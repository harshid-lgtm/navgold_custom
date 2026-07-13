# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count

# Old row sources that mean "feed-owned" (anything other than Manual / Auto-Sync / empty).
NON_MANUAL_SYNC_SOURCES = {
	"Supplier-Sync",
	"PO-Sync",
	"FBA-Transfer",
	"Amazon-Sync",
	"Item-Tax-Sync",
	"Shipping-Sync",
}


def execute():
	if not frappe.db.table_exists("Cost Sub Type"):
		return

	# Capture historical per-sub-type source spread BEFORE value_mode is collapsed to Auto/Manual.
	log_conflicting_historical_sources()

	remap_value_mode()
	drop_orphan_columns()


def pre_remodel_source_present():
	# The pre_model_sync rename_cost_source_to_value_mode patch bails on this Frappe version
	# (`rename_field` needs the new field in meta, which isn't synced yet), so model-sync creates
	# value_mode fresh at its "Auto" default and the true pre-remodel policy survives in the orphaned
	# `source` column. When it's present, it — not the defaulted value_mode — is the source of truth.
	return "source" in frappe.db.get_table_columns("Amazon Product Cost")


def remap_value_mode():
	cost = DocType("Amazon Product Cost")
	origin = cost.source if pre_remodel_source_present() else cost.value_mode

	# Human-pinned "Manual" stays Manual; every feed/other/empty value becomes Auto (feed/default-owned).
	# Two disjoint updates so a NULL origin still lands on Auto.
	frappe.qb.update(cost).set(cost.value_mode, "Manual").where(origin == "Manual").run()
	frappe.qb.update(cost).set(cost.value_mode, "Auto").where((origin != "Manual") | origin.isnull()).run()


def drop_orphan_columns():
	# Frappe leaves removed fields' columns behind; drop the dead ones this remodel retired.
	if "is_default" in frappe.db.get_table_columns("Cost Sub Type"):
		frappe.db.sql_ddl("ALTER TABLE `tabCost Sub Type` DROP COLUMN `is_default`")
	if "source" in frappe.db.get_table_columns("Amazon Cost Set Template Item"):
		frappe.db.sql_ddl("ALTER TABLE `tabAmazon Cost Set Template Item` DROP COLUMN `source`")
	# The orphaned Amazon Product Cost.source column has served its purpose (remap read it above); drop it.
	if "source" in frappe.db.get_table_columns("Amazon Product Cost"):
		frappe.db.sql_ddl("ALTER TABLE `tabAmazon Product Cost` DROP COLUMN `source`")


def log_conflicting_historical_sources():
	cost = DocType("Amazon Product Cost")
	origin = cost.source if pre_remodel_source_present() else cost.value_mode
	rows = (
		frappe.qb.from_(cost)
		.select(cost.cost_sub_type, origin.as_("historical_source"), Count(cost.name).as_("row_count"))
		.where(cost.cost_sub_type.notnull())
		.where(origin.isin(list(NON_MANUAL_SYNC_SOURCES)))
		.groupby(cost.cost_sub_type, origin)
	).run(as_dict=True)

	source_counts_by_sub_type: dict[str, dict[str, int]] = {}
	for row in rows:
		source_counts_by_sub_type.setdefault(row.cost_sub_type, {})[row.historical_source] = row.row_count

	conflicts = {
		sub_type: counts for sub_type, counts in source_counts_by_sub_type.items() if len(counts) > 1
	}
	if not conflicts:
		return

	report_lines = [
		"Cost Sub Types whose historical rows used 2+ different feed sources (1:1 assumption broke)."
	]
	report_lines.append(
		"The cost-tree migration resolves each source from the hardcoded map regardless; review these by hand:"
	)
	for sub_type, counts in conflicts.items():
		most_frequent = max(counts, key=counts.get)
		spread = ", ".join(f"{source}={count}" for source, count in sorted(counts.items()))
		report_lines.append(f"  {sub_type}: {spread} (most frequent: {most_frequent})")

	frappe.log_error("\n".join(report_lines), "Cost Source Remodel: ambiguous sub-type sources")
