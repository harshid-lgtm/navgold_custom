# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import make_item, receive_stock


def make_warehouse(warehouse_name, company, walk_route):
	existing = frappe.db.exists("Warehouse", {"warehouse_name": warehouse_name, "company": company})
	if existing:
		return existing
	parent_warehouse = frappe.get_all(
		"Warehouse",
		filters={"company": company, "is_group": 1},
		limit=1,
		pluck="name",
	)[0]
	warehouse = frappe.new_doc("Warehouse")
	warehouse.warehouse_name = warehouse_name
	warehouse.company = company
	warehouse.parent_warehouse = parent_warehouse
	warehouse.custom_walk_route = walk_route
	warehouse.insert()
	return warehouse.name


def make_pick_list(company, item_code, qty):
	pick_list = frappe.new_doc("Pick List")
	pick_list.company = company
	pick_list.purpose = "Material Transfer"
	pick_list.append(
		"locations",
		{
			"item_code": item_code,
			"qty": qty,
			"stock_qty": qty,
			"uom": "Nos",
			"stock_uom": "Nos",
			"conversion_factor": 1,
		},
	)
	pick_list.save()
	return pick_list


class TestPickListWalkRouteSort(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = (
			frappe.defaults.get_global_default("company")
			or frappe.get_all("Company", limit=1, pluck="name")[0]
		)
		# Receipt order deliberately differs from walk-route order so FIFO
		# allocation alone cannot produce the expected sequence.
		cls.warehouse_late = make_warehouse("Walk Route Test Late", cls.company, 30)
		cls.warehouse_mid = make_warehouse("Walk Route Test Mid", cls.company, 10.5)
		cls.warehouse_early = make_warehouse("Walk Route Test Early", cls.company, 10)
		cls.warehouse_unrouted = make_warehouse("Walk Route Test Unrouted", cls.company, 0)

		cls.routed_item = make_item("WALK-ROUTE-ITEM", 1)
		receive_stock(cls.routed_item, 5, cls.warehouse_late, cls.company)
		receive_stock(cls.routed_item, 5, cls.warehouse_mid, cls.company)
		receive_stock(cls.routed_item, 5, cls.warehouse_early, cls.company)

		cls.partly_routed_item = make_item("WALK-ROUTE-ITEM-UNROUTED", 1)
		receive_stock(cls.partly_routed_item, 5, cls.warehouse_unrouted, cls.company)
		receive_stock(cls.partly_routed_item, 5, cls.warehouse_late, cls.company)

	def test_locations_sorted_by_walk_route_with_decimal_routes(self):
		pick_list = make_pick_list(self.company, self.routed_item, 12)

		warehouses = [row.warehouse for row in pick_list.locations]
		self.assertEqual(warehouses, [self.warehouse_early, self.warehouse_mid, self.warehouse_late])
		self.assertEqual([row.custom_walk_route for row in pick_list.locations], [10, 10.5, 30])
		self.assertEqual([row.idx for row in pick_list.locations], [1, 2, 3])

		# The order must survive a reload — a sort that skips idx renumbering
		# only reorders in memory and reverts on the next fetch.
		reloaded = frappe.get_doc("Pick List", pick_list.name)
		self.assertEqual(
			[row.warehouse for row in reloaded.locations],
			[self.warehouse_early, self.warehouse_mid, self.warehouse_late],
		)

	def test_unrouted_warehouse_sorts_last(self):
		pick_list = make_pick_list(self.company, self.partly_routed_item, 10)

		warehouses = [row.warehouse for row in pick_list.locations]
		self.assertEqual(warehouses, [self.warehouse_late, self.warehouse_unrouted])
