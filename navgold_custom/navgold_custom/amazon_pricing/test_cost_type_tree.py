# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

GROUP_NAME = "Test Tree Absolute"


def ensure_group():
	if not frappe.db.exists("Cost Type", GROUP_NAME):
		group = frappe.new_doc("Cost Type")
		group.cost_type_name = GROUP_NAME
		group.is_group = 1
		group.calculation_type = "Absolute"
		group.insert()
	return GROUP_NAME


class TestCostTypeTree(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_group()

	def test_leaf_autoname_matches_old_sub_type_format(self):
		leaf = frappe.new_doc("Cost Type")
		leaf.cost_type_name = "Overhead"
		leaf.is_group = 0
		leaf.parent_cost_type = GROUP_NAME
		leaf.default_value = 3.5
		leaf.insert()
		self.assertEqual(leaf.name, f"{GROUP_NAME}-Overhead")

	def test_group_forbids_source_and_default(self):
		group = frappe.new_doc("Cost Type")
		group.cost_type_name = "Test Tree Group No Policy"
		group.is_group = 1
		group.calculation_type = "Relative"
		group.source = "Supplier-Sync"
		group.default_value = 9
		group.insert()
		self.assertEqual(group.source, "")
		self.assertEqual(group.default_value, 0)

	def test_fed_leaf_forbids_default_value(self):
		leaf = frappe.new_doc("Cost Type")
		leaf.cost_type_name = "Bad Fed"
		leaf.is_group = 0
		leaf.parent_cost_type = GROUP_NAME
		leaf.source = "Supplier-Sync"
		leaf.default_value = 12
		with self.assertRaises(frappe.ValidationError):
			leaf.insert()
