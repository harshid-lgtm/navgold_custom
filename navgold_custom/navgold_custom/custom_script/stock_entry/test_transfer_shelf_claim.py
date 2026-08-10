# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import make_item
from navgold_custom.navgold_custom.custom_script.stock_entry.test_fba_clearing import (
	COMPANY,
	create_test_warehouse,
)
from navgold_custom.patches.v1.release_transferred_shelf_claims import (
	execute as release_transferred_shelf_claims,
)


class TestTransferShelfClaim(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.source_warehouse = create_test_warehouse("Test Shelf Claim Source")
		cls.target_warehouse = create_test_warehouse("Test Shelf Claim Target")

	def setUp(self):
		super().setUp()
		self.settled_item = self.make_test_item("settled")
		self.straggler_item = self.make_test_item("straggler")

	def make_test_item(self, role):
		return make_item(f"TEST-SHELF-CLAIM-{self._testMethodName}-{role}", is_stock_item=1)

	def receive_stock(self, item_code, qty):
		receipt = frappe.new_doc("Stock Entry")
		receipt.stock_entry_type = "Material Receipt"
		receipt.company = COMPANY
		receipt.append(
			"items",
			{
				"item_code": item_code,
				"qty": qty,
				"t_warehouse": self.source_warehouse,
				"basic_rate": 100,
			},
		)
		receipt.insert()
		receipt.submit()
		return receipt

	def make_pick_list(self, rows, purpose="Material Transfer"):
		pick_list = frappe.new_doc("Pick List")
		pick_list.company = COMPANY
		pick_list.purpose = purpose
		pick_list.pick_manually = 1
		for item_code, qty in rows:
			pick_list.append(
				"locations",
				{
					"item_code": item_code,
					"warehouse": self.source_warehouse,
					"qty": qty,
					"stock_qty": qty,
					"picked_qty": qty,
					"uom": "Nos",
					"stock_uom": "Nos",
					"conversion_factor": 1,
				},
			)
		pick_list.insert()
		pick_list.submit()
		return pick_list

	def transfer_row(self, pick_list, location, qty):
		transfer = frappe.new_doc("Stock Entry")
		transfer.stock_entry_type = "Material Transfer"
		transfer.company = COMPANY
		transfer.pick_list = pick_list.name
		transfer.append(
			"items",
			{
				"item_code": location.item_code,
				"qty": qty,
				"s_warehouse": self.source_warehouse,
				"t_warehouse": self.target_warehouse,
				"pick_list_item": location.name,
			},
		)
		transfer.insert()
		transfer.submit()
		return transfer

	def get_location(self, pick_list_name, item_code):
		return frappe.db.get_value(
			"Pick List Item",
			{"parent": pick_list_name, "item_code": item_code},
			["name", "picked_qty", "delivered_qty", "transferred_qty"],
			as_dict=True,
		)

	def get_claimed_qty(self, item_code):
		reader = frappe.new_doc("Pick List")
		reader.company = COMPANY
		reader.purpose = "Delivery"
		picked_items = reader.get_picked_items_details([frappe._dict(item_code=item_code)])
		warehouse_claims = picked_items.get(item_code) or {}
		return flt(warehouse_claims.get(self.source_warehouse, {}).get("picked_qty"))

	def test_a_transferred_row_releases_its_shelf_claim(self):
		self.receive_stock(self.settled_item, 10)
		self.receive_stock(self.straggler_item, 4)
		pick_list = self.make_pick_list([(self.settled_item, 10), (self.straggler_item, 4)])

		settled_location = next(row for row in pick_list.locations if row.item_code == self.settled_item)
		self.transfer_row(pick_list, settled_location, 10)

		settled_row = self.get_location(pick_list.name, self.settled_item)
		self.assertEqual(flt(settled_row.transferred_qty), 10)
		self.assertEqual(flt(settled_row.delivered_qty), 10)

		straggler_row = self.get_location(pick_list.name, self.straggler_item)
		self.assertEqual(flt(straggler_row.transferred_qty), 0)
		self.assertEqual(flt(straggler_row.delivered_qty), 0)

	def test_a_straggler_row_no_longer_freezes_the_stock_beside_it(self):
		self.receive_stock(self.settled_item, 10)
		self.receive_stock(self.straggler_item, 4)
		pick_list = self.make_pick_list([(self.settled_item, 10), (self.straggler_item, 4)])

		settled_location = next(row for row in pick_list.locations if row.item_code == self.settled_item)
		self.transfer_row(pick_list, settled_location, 10)

		self.assertEqual(self.get_claimed_qty(self.settled_item), 0)
		self.assertEqual(self.get_claimed_qty(self.straggler_item), 4)

	def test_a_partial_transfer_keeps_claiming_only_what_stayed(self):
		self.receive_stock(self.settled_item, 36)
		pick_list = self.make_pick_list([(self.settled_item, 36)])

		self.transfer_row(pick_list, pick_list.locations[0], 1)

		self.assertEqual(self.get_claimed_qty(self.settled_item), 35)

	def test_cancelling_the_transfer_puts_the_claim_back(self):
		self.receive_stock(self.settled_item, 10)
		self.receive_stock(self.straggler_item, 4)
		pick_list = self.make_pick_list([(self.settled_item, 10), (self.straggler_item, 4)])

		settled_location = next(row for row in pick_list.locations if row.item_code == self.settled_item)
		transfer = self.transfer_row(pick_list, settled_location, 10)
		self.assertEqual(self.get_claimed_qty(self.settled_item), 0)

		transfer.cancel()

		self.assertEqual(self.get_claimed_qty(self.settled_item), 10)

	def test_the_backfill_releases_rows_stranded_before_the_fix(self):
		self.receive_stock(self.settled_item, 10)
		self.receive_stock(self.straggler_item, 4)
		pick_list = self.make_pick_list([(self.settled_item, 10), (self.straggler_item, 4)])

		settled_location = next(row for row in pick_list.locations if row.item_code == self.settled_item)
		self.transfer_row(pick_list, settled_location, 10)

		frappe.db.set_value("Pick List Item", settled_location.name, "delivered_qty", 0)
		self.assertEqual(self.get_claimed_qty(self.settled_item), 10)

		release_transferred_shelf_claims()

		self.assertEqual(self.get_claimed_qty(self.settled_item), 0)
		self.assertEqual(self.get_claimed_qty(self.straggler_item), 4)

	def test_a_delivery_pick_list_is_left_alone(self):
		self.receive_stock(self.settled_item, 5)
		pick_list = self.make_pick_list([(self.settled_item, 5)], purpose="Delivery")

		self.transfer_row(pick_list, pick_list.locations[0], 5)

		delivery_row = self.get_location(pick_list.name, self.settled_item)
		self.assertEqual(flt(delivery_row.delivered_qty), 0)
		self.assertEqual(self.get_claimed_qty(self.settled_item), 5)
