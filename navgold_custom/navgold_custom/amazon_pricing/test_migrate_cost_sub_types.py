# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.patches.v1.migrate_cost_sub_types_into_cost_type import execute

GROUP = "Test Migrate Absolute"
SUB_TYPES = [
	{"sub_type_name": "Migrate Overhead", "source": "", "default_value": 4.5},
	{"sub_type_name": "Migrate Referral", "source": "Amazon-Sync", "default_value": 0},
]


class TestMigrateCostSubTypes(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Cost Type", GROUP):
			group = frappe.new_doc("Cost Type")
			group.cost_type_name = GROUP
			group.is_group = 1
			group.calculation_type = "Absolute"
			group.insert()
		for sub in SUB_TYPES:
			name = f"{GROUP}-{sub['sub_type_name']}"
			if not frappe.db.exists("Cost Sub Type", name):
				doc = frappe.new_doc("Cost Sub Type")
				doc.cost_type = GROUP
				doc.update(sub)
				doc.insert()

	def test_shadow_leaf_created_per_sub_type_with_matching_policy(self):
		execute()
		for sub in SUB_TYPES:
			name = f"{GROUP}-{sub['sub_type_name']}"
			leaf = frappe.db.get_value(
				"Cost Type", name, ["source", "default_value", "parent_cost_type", "is_group"], as_dict=True
			)
			self.assertIsNotNone(leaf, f"no shadow leaf for {name}")
			self.assertEqual(leaf.is_group, 0)
			self.assertEqual(leaf.parent_cost_type, GROUP)
			self.assertEqual(leaf.source or "", sub["source"])
			self.assertEqual(float(leaf.default_value or 0), float(sub["default_value"]))

	def test_count_parity(self):
		execute()
		self.assertEqual(
			frappe.db.count("Cost Type", {"is_group": 0}),
			frappe.db.count("Cost Sub Type"),
		)

	def test_rerun_is_idempotent(self):
		execute()
		before = frappe.db.count("Cost Type")
		execute()
		self.assertEqual(frappe.db.count("Cost Type"), before)
