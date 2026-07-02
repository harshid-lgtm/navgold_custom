# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count

# The source policy relocated from each Amazon Product Cost row up to its Cost Sub Type. This map was the
# hardcoded fan-out in Bulk Reset Purchase Cost Source; it is the source of truth for the backfill.
SUB_TYPE_TO_AUTO_SOURCE = {
	"Purchase Cost-Warehouse Cost": "PO-Sync",
	"Purchase Cost-Top Supplier": "Supplier-Sync",
	"Purchase Cost-Last Transfer": "FBA-Transfer",
	"Fulfillment Cost-Estimated FBA Fee": "Amazon-Sync",
	"Fulfillment Cost-Domestic Regular": "Shipping-Sync",
	"Fulfillment Cost-Amazon Shipping": "Shipping-Sync",
}

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

	backfill_sub_type_source()
	remap_value_mode()
	drop_orphan_columns()


def backfill_sub_type_source():
	vat_cost_types = set(frappe.get_all("Cost Type", filters={"calculation_type": "VAT"}, pluck="name"))

	sub_types = frappe.get_all("Cost Sub Type", fields=["name", "cost_type", "source", "default_value"])
	for sub_type in sub_types:
		target_source = SUB_TYPE_TO_AUTO_SOURCE.get(sub_type.name)
		if not target_source and sub_type.cost_type in vat_cost_types:
			target_source = "Item-Tax-Sync"

		# No feed mapping → self-contained; leave source empty and keep its existing default_value.
		if not target_source:
			continue

		# Idempotent: already migrated rows map to themselves; clearing default_value satisfies validate().
		frappe.db.set_value(
			"Cost Sub Type",
			sub_type.name,
			{"source": target_source, "default_value": 0},
			update_modified=False,
		)


def remap_value_mode():
	cost = DocType("Amazon Product Cost")

	# Old "Manual" stays Manual (human-pinned); every other old source value becomes Auto (feed/default-owned).
	frappe.qb.update(cost).set(cost.value_mode, "Manual").where(cost.value_mode == "Manual").run()
	frappe.qb.update(cost).set(cost.value_mode, "Auto").where(cost.value_mode != "Manual").run()


def drop_orphan_columns():
	# Frappe leaves removed fields' columns behind; drop the dead ones this remodel retired.
	if "is_default" in frappe.db.get_table_columns("Cost Sub Type"):
		frappe.db.sql_ddl("ALTER TABLE `tabCost Sub Type` DROP COLUMN `is_default`")
	if "source" in frappe.db.get_table_columns("Amazon Cost Set Template Item"):
		frappe.db.sql_ddl("ALTER TABLE `tabAmazon Cost Set Template Item` DROP COLUMN `source`")


def log_conflicting_historical_sources():
	cost = DocType("Amazon Product Cost")
	rows = (
		frappe.qb.from_(cost)
		.select(
			cost.cost_sub_type, cost.value_mode.as_("historical_source"), Count(cost.name).as_("row_count")
		)
		.where(cost.cost_sub_type.notnull())
		.where(cost.value_mode.isin(list(NON_MANUAL_SYNC_SOURCES)))
		.groupby(cost.cost_sub_type, cost.value_mode)
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
		"Backfill uses the hardcoded SUB_TYPE_TO_AUTO_SOURCE map regardless; review these by hand:"
	)
	for sub_type, counts in conflicts.items():
		most_frequent = max(counts, key=counts.get)
		spread = ", ".join(f"{source}={count}" for source, count in sorted(counts.items()))
		report_lines.append(f"  {sub_type}: {spread} (most frequent: {most_frequent})")

	frappe.log_error("\n".join(report_lines), "Cost Source Remodel: ambiguous sub-type sources")
