# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.query_builder import DocType
from frappe.utils.nestedset import rebuild_tree


def execute():
	# Every pre-existing Cost Type becomes a group node; leaves arrive in a later slice. Idempotent:
	# already-group rows are set to the same value, so a re-run is a no-op.
	cost_type = DocType("Cost Type")
	frappe.qb.update(cost_type).set(cost_type.is_group, 1).run()

	rebuild_tree("Cost Type")
