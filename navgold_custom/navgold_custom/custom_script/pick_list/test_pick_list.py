# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import make_item

# erpnext.stock.doctype.warehouse.test_warehouse.create_warehouse is deliberately NOT
# reused: importing erpnext test modules triggers their test-record chain, which creates
# a Supplier that fails this app's lead-time validation hook during test discovery.


def receive_stock(item_code, qty_per_warehouse, warehouses, company):
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.stock_entry_type = "Material Receipt"
	stock_entry.company = company
	for warehouse in warehouses:
		stock_entry.append(
			"items",
			{
				"item_code": item_code,
				"qty": qty_per_warehouse,
				"t_warehouse": warehouse,
				"basic_rate": 10,
			},
		)
	stock_entry.insert()
	stock_entry.submit()


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
		parent_warehouse = frappe.get_all(
			"Warehouse",
			filters={"company": cls.company, "is_group": 1},
			limit=1,
			pluck="name",
		)[0]

		def make_test_warehouse(warehouse_name, walk_route):
			warehouse = frappe.new_doc("Warehouse")
			warehouse.warehouse_name = warehouse_name
			warehouse.company = cls.company
			warehouse.parent_warehouse = parent_warehouse
			warehouse.custom_walk_route = walk_route
			warehouse.insert()
			return warehouse.name

		cls.warehouse_late = make_test_warehouse("Walk Route Test Late", 30)
		cls.warehouse_mid = make_test_warehouse("Walk Route Test Mid", 10.5)
		cls.warehouse_early = make_test_warehouse("Walk Route Test Early", 10)
		cls.warehouse_unrouted = make_test_warehouse("Walk Route Test Unrouted", 0)

		# Receipt row order deliberately differs from walk-route order so FIFO
		# allocation alone cannot produce the expected sequence.
		cls.routed_item = make_item("WALK-ROUTE-ITEM", 1)
		receive_stock(
			cls.routed_item, 5, [cls.warehouse_late, cls.warehouse_mid, cls.warehouse_early], cls.company
		)

		cls.partly_routed_item = make_item("WALK-ROUTE-ITEM-UNROUTED", 1)
		receive_stock(cls.partly_routed_item, 5, [cls.warehouse_unrouted, cls.warehouse_late], cls.company)

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
