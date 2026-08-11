# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.test_bundle_stock import (
	get_stock_warehouse,
	make_bundle,
	make_item,
	receive_stock,
)
from navgold_custom.navgold_custom.custom_script.stock_entry.test_fba_clearing import (
	create_test_warehouse,
)
from navgold_custom.navgold_custom.report.navgold_forecast_report.navgold_forecast_report import (
	apply_transfer_shortfall_decisions,
	create_material_request_doc,
	expand_selection,
	plan_purchase_orders,
	resolve_transfer_shortfalls,
	submit_purchase_orders,
)

DAYS_OF_WEEK = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def make_supplier(supplier_name):
	supplier = frappe.new_doc("Supplier")
	supplier.supplier_name = supplier_name
	supplier.custom_rank = 1
	supplier.extend(
		"custom_lead_time",
		[{"day": day, "lead_time_before_12": 1, "lead_time_after_12": 2} for day in DAYS_OF_WEEK],
	)
	supplier.insert()
	return supplier.name


def make_supplier_price(item_code, supplier, price, uom="Nos"):
	supplier_price = frappe.new_doc("Supplier Item Price and Stock")
	# Skips the site-wide rank recompute this row would otherwise kick off synchronously in tests.
	supplier_price.flags.bulk_entry = True
	supplier_price.item = item_code
	supplier_price.supplier = supplier
	supplier_price.uom = uom
	supplier_price.price = price
	supplier_price.stock_qty = 100
	supplier_price.currency = frappe.db.get_default("currency") or "GBP"
	supplier_price.insert()
	frappe.db.set_value("Supplier Item Price and Stock", supplier_price.name, "rank", 1)
	return supplier_price.name


def add_uom_conversion(item_code, uom, conversion_factor):
	if not frappe.db.exists("UOM", uom):
		unit = frappe.new_doc("UOM")
		unit.uom_name = uom
		unit.insert()
	item = frappe.get_doc("Item", item_code)
	item.append("uoms", {"uom": uom, "conversion_factor": conversion_factor})
	item.save()


class TestForecastReportActions(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.warehouse, cls.company = get_stock_warehouse()
		suffix = frappe.generate_hash(length=6)

		cls.plain_item = make_item(f"NG-PLAIN-{suffix}", is_stock_item=1)
		cls.component_a = make_item(f"NG-COMP-A-{suffix}", is_stock_item=1)
		cls.component_b = make_item(f"NG-COMP-B-{suffix}", is_stock_item=1)

		cls.bundle_item = make_item(f"NG-BUNDLE-{suffix}", is_stock_item=0)
		make_bundle(cls.bundle_item, [(cls.component_a, 2), (cls.component_b, 3)])

		# Same bundle sold by the case: the selected qty scales by the parent conversion factor.
		cls.pack_bundle_item = make_item(f"NG-PACKBUNDLE-{suffix}", is_stock_item=0)
		make_bundle(cls.pack_bundle_item, [(cls.component_a, 3)])
		add_uom_conversion(cls.pack_bundle_item, "NG Pack of 4", 4)

		# Non-stock parent with no Product Bundle behind it.
		cls.orphan_item = make_item(f"NG-ORPHAN-{suffix}", is_stock_item=0)

		cls.supplier_one = make_supplier(f"NG Supplier One {suffix}")
		cls.supplier_two = make_supplier(f"NG Supplier Two {suffix}")

		# Stands in for the configured AFN warehouse: stock sitting there is already at Amazon.
		cls.afn_warehouse = create_test_warehouse("NG Forecast AFN")

	def select(self, rows):
		"""rows: list of (item_code, uom, qty) -> (selected_items, sku_info) as the report sends them."""
		selected_items = []
		sku_info = {}
		for index, (item_code, uom, qty) in enumerate(rows):
			sku = f"NG-SKU-{index}"
			selected_items.append({"sku": sku, "quantity_to_transfer": qty})
			sku_info[sku] = {"item": item_code, "uom": uom}
		return selected_items, sku_info

	def expand(self, rows):
		return expand_selection(*self.select(rows))

	def make_own_bundle(self, component_quantities):
		"""A bundle nobody else touches. The runner shares one transaction across the class, so a
		supplier price written by one test is still there for the next one."""
		suffix = frappe.generate_hash(length=6)
		components = [
			make_item(f"NG-OWN-C{index}-{suffix}", is_stock_item=1)
			for index in range(len(component_quantities))
		]
		parent = make_item(f"NG-OWN-B-{suffix}", is_stock_item=0)
		make_bundle(parent, list(zip(components, component_quantities, strict=True)))
		return parent, components

	def make_stocked_item(self, qty):
		"""An item nobody else in the class has stocked, so availability is exactly ``qty``."""
		item_code = make_item(f"NG-STOCK-{frappe.generate_hash(length=6)}", is_stock_item=1)
		receive_stock(item_code, qty, self.warehouse, self.company)
		return item_code

	def make_open_pick_list(self, item_code, qty):
		pick_list = frappe.new_doc("Pick List")
		pick_list.company = self.company
		pick_list.purpose = "Material Transfer"
		pick_list.pick_manually = 1
		pick_list.append(
			"locations",
			{
				"item_code": item_code,
				"warehouse": self.warehouse,
				"qty": qty,
				"stock_qty": qty,
				"uom": "Nos",
				"stock_uom": "Nos",
				"conversion_factor": 1,
			},
		)
		pick_list.insert()
		return pick_list.name

	def test_plain_sku_expands_to_a_single_row(self):
		rows = self.expand([(self.plain_item, "Nos", 5)])
		self.assertEqual(rows, [{"sku": "NG-SKU-0", "item_code": self.plain_item, "qty": 5, "uom": "Nos"}])

	def test_bundle_expands_to_components_with_scaled_qty(self):
		rows = self.expand([(self.bundle_item, "Nos", 4)])
		self.assertEqual(
			rows,
			[
				{"sku": "NG-SKU-0", "item_code": self.component_a, "qty": 8, "uom": ""},
				{"sku": "NG-SKU-0", "item_code": self.component_b, "qty": 12, "uom": ""},
			],
		)

	def test_bundle_in_a_pack_uom_scales_by_the_parent_conversion_factor(self):
		# 2 packs x 4 bundles per pack x 3 components per bundle = 24
		rows = self.expand([(self.pack_bundle_item, "NG Pack of 4", 2)])
		self.assertEqual(rows, [{"sku": "NG-SKU-0", "item_code": self.component_a, "qty": 24, "uom": ""}])

	def test_non_stock_sku_without_an_active_bundle_is_rejected(self):
		with self.assertRaises(frappe.ValidationError) as raised:
			self.expand([(self.orphan_item, "Nos", 3)])
		self.assertIn(self.orphan_item, str(raised.exception))

	def test_sku_without_an_item_link_is_named_in_the_error(self):
		with self.assertRaises(frappe.ValidationError) as raised:
			expand_selection([{"sku": "NG-UNLINKED", "quantity_to_transfer": 5}], {})
		self.assertIn("NG-UNLINKED", str(raised.exception))

	def test_selection_without_quantity_is_rejected_on_quantity(self):
		with self.assertRaises(frappe.ValidationError) as raised:
			self.expand([(self.plain_item, "Nos", 0)])
		self.assertIn("Quantity to Transfer", str(raised.exception))

	def test_unlinked_sku_alongside_a_valid_one_is_skipped_not_silently_dropped(self):
		selected_items, sku_info = self.select([(self.plain_item, "Nos", 5)])
		selected_items.append({"sku": "NG-UNLINKED", "quantity_to_transfer": 9})

		frappe.clear_messages()
		rows = expand_selection(selected_items, sku_info)

		self.assertEqual([row["item_code"] for row in rows], [self.plain_item])
		messages = [entry["message"] for entry in frappe.get_message_log()]
		self.assertTrue(
			any("NG-UNLINKED" in message for message in messages),
			f"skipped SKU was not reported to the user: {messages}",
		)

	def test_material_request_lands_components_in_their_own_stock_uom(self):
		transfer_rows = self.expand([(self.plain_item, "Nos", 5), (self.bundle_item, "Nos", 10)])
		material_request, item_map = create_material_request_doc(transfer_rows, self.company, self.warehouse)

		self.assertEqual(material_request.docstatus, 1)
		self.assertEqual(
			[(row.item_code, row.qty, row.uom, row.stock_qty) for row in material_request.items],
			[
				(self.plain_item, 5.0, "Nos", 5.0),
				(self.component_a, 20.0, "Nos", 20.0),
				(self.component_b, 30.0, "Nos", 30.0),
			],
		)
		self.assertEqual(
			set(item_map),
			{
				("NG-SKU-0", self.plain_item),
				("NG-SKU-1", self.component_a),
				("NG-SKU-1", self.component_b),
			},
		)

	def test_purchase_orders_split_by_component_supplier(self):
		bundle, (component_one, component_two) = self.make_own_bundle([2, 3])
		make_supplier_price(component_one, self.supplier_one, 1.5)
		make_supplier_price(component_two, self.supplier_two, 2.5)

		transfer_rows = self.expand([(bundle, "Nos", 10)])
		supplier_groups = plan_purchase_orders(transfer_rows)
		created, purchase_order_map = submit_purchase_orders(supplier_groups, self.company, self.warehouse)

		self.assertEqual(len(created), 2)
		ordered = {}
		for purchase_order_name in created:
			purchase_order = frappe.get_doc("Purchase Order", purchase_order_name)
			for row in purchase_order.items:
				ordered[row.item_code] = (purchase_order.supplier, row.qty, row.rate)

		self.assertEqual(ordered[component_one], (self.supplier_one, 20.0, 1.5))
		self.assertEqual(ordered[component_two], (self.supplier_two, 30.0, 2.5))
		self.assertEqual(set(purchase_order_map), {("NG-SKU-0", component_one), ("NG-SKU-0", component_two)})

	def test_component_without_a_supplier_is_left_off_but_the_rest_is_ordered(self):
		bundle, (component_one, _component_two) = self.make_own_bundle([2, 3])
		make_supplier_price(component_one, self.supplier_one, 1.5)

		transfer_rows = self.expand([(bundle, "Nos", 10)])
		supplier_groups = plan_purchase_orders(transfer_rows)
		created, _purchase_order_map = submit_purchase_orders(supplier_groups, self.company, self.warehouse)

		self.assertEqual(len(created), 1)
		purchase_order = frappe.get_doc("Purchase Order", created[0])
		self.assertEqual([row.item_code for row in purchase_order.items], [component_one])

	def test_nothing_purchasable_throws_before_any_order(self):
		bundle, (component_one, _component_two) = self.make_own_bundle([2, 3])
		with self.assertRaises(frappe.ValidationError) as raised:
			plan_purchase_orders(self.expand([(bundle, "Nos", 10)]))
		self.assertIn(component_one, str(raised.exception))

	def test_shortfalls_are_empty_when_the_shelves_cover_the_selection(self):
		item_code = self.make_stocked_item(10)
		transfer_rows = self.expand([(item_code, "Nos", 10)])

		kept_rows, shortfalls = resolve_transfer_shortfalls(transfer_rows, self.company, self.afn_warehouse)

		self.assertEqual(shortfalls, [])
		self.assertEqual(kept_rows, transfer_rows)

	def test_short_row_reports_what_the_shelves_can_actually_give(self):
		item_code = self.make_stocked_item(39)
		transfer_rows = self.expand([(item_code, "Nos", 60)])

		shortfalls = resolve_transfer_shortfalls(transfer_rows, self.company, self.afn_warehouse)[1]

		self.assertEqual(len(shortfalls), 1)
		self.assertEqual(shortfalls[0]["item_code"], item_code)
		self.assertEqual(shortfalls[0]["requested_qty"], 60.0)
		self.assertEqual(shortfalls[0]["available_qty"], 39.0)
		self.assertEqual(shortfalls[0]["transferable_qty"], 39.0)

	def test_short_pack_row_rounds_down_to_whole_packs(self):
		# 39 singles against a pack of 6 is 6 whole packs — 6.5 packs is the fraction that strands
		# the transfer at pick and Stock Entry time.
		item_code = self.make_stocked_item(39)
		add_uom_conversion(item_code, "NG Pack of 6", 6)
		transfer_rows = self.expand([(item_code, "NG Pack of 6", 10)])

		shortfalls = resolve_transfer_shortfalls(transfer_rows, self.company, self.afn_warehouse)[1]

		self.assertEqual(shortfalls[0]["available_qty"], 39.0)
		self.assertEqual(shortfalls[0]["transferable_qty"], 6.0)
		self.assertEqual(shortfalls[0]["uom"], "NG Pack of 6")

	def test_stock_already_at_amazon_is_not_available_to_send_to_amazon(self):
		item_code = self.make_stocked_item(4)
		receive_stock(item_code, 50, self.afn_warehouse, self.company)
		transfer_rows = self.expand([(item_code, "Nos", 20)])

		shortfalls = resolve_transfer_shortfalls(transfer_rows, self.company, self.afn_warehouse)[1]

		self.assertEqual(shortfalls[0]["available_qty"], 4.0)

	def test_stock_claimed_by_an_open_pick_list_is_not_available(self):
		item_code = self.make_stocked_item(30)
		self.make_open_pick_list(item_code, 25)
		transfer_rows = self.expand([(item_code, "Nos", 30)])

		shortfalls = resolve_transfer_shortfalls(transfer_rows, self.company, self.afn_warehouse)[1]

		self.assertEqual(shortfalls[0]["available_qty"], 5.0)

	def test_two_skus_of_one_item_cannot_both_claim_the_same_stock(self):
		item_code = self.make_stocked_item(10)
		transfer_rows = self.expand([(item_code, "Nos", 8), (item_code, "Nos", 8)])

		shortfalls = resolve_transfer_shortfalls(transfer_rows, self.company, self.afn_warehouse)[1]

		# The first row takes 8 of the 10, so the second is short — not two rows of 8 out of one pile.
		self.assertEqual([row["sku"] for row in shortfalls], ["NG-SKU-1"])
		self.assertEqual(shortfalls[0]["available_qty"], 2.0)

	def test_accepted_row_is_trimmed_to_what_we_have(self):
		item_code = self.make_stocked_item(39)
		transfer_rows = self.expand([(item_code, "Nos", 60)])

		kept_rows = apply_transfer_shortfall_decisions(
			transfer_rows, self.company, self.afn_warehouse, {f"NG-SKU-0::{item_code}": 1}
		)

		self.assertEqual(kept_rows, [{"sku": "NG-SKU-0", "item_code": item_code, "qty": 39.0, "uom": "Nos"}])

	def test_rejected_row_is_left_off_entirely(self):
		short_item = self.make_stocked_item(2)
		stocked_item = self.make_stocked_item(50)
		transfer_rows = self.expand([(short_item, "Nos", 60), (stocked_item, "Nos", 10)])

		kept_rows = apply_transfer_shortfall_decisions(
			transfer_rows, self.company, self.afn_warehouse, {f"NG-SKU-0::{short_item}": 0}
		)

		self.assertEqual([row["item_code"] for row in kept_rows], [stocked_item])

	def test_accepted_row_without_a_whole_pack_is_left_off(self):
		item_code = self.make_stocked_item(4)
		add_uom_conversion(item_code, "NG Pack of 6", 6)
		transfer_rows = self.expand([(item_code, "NG Pack of 6", 10)])

		kept_rows, shortfalls = resolve_transfer_shortfalls(
			transfer_rows, self.company, self.afn_warehouse, {f"NG-SKU-0::{item_code}": True}
		)

		self.assertEqual(shortfalls[0]["transferable_qty"], 0.0)
		self.assertEqual(kept_rows, [])

	def test_short_row_nobody_answered_for_is_still_requested_whole(self):
		item_code = self.make_stocked_item(5)
		transfer_rows = self.expand([(item_code, "Nos", 60)])

		kept_rows = resolve_transfer_shortfalls(transfer_rows, self.company, self.afn_warehouse, {})[0]

		self.assertEqual(kept_rows, transfer_rows)

	def test_leaving_out_every_row_throws_instead_of_creating_an_empty_transfer(self):
		item_code = self.make_stocked_item(2)
		transfer_rows = self.expand([(item_code, "Nos", 60)])

		with self.assertRaises(frappe.ValidationError) as raised:
			apply_transfer_shortfall_decisions(
				transfer_rows, self.company, self.afn_warehouse, {f"NG-SKU-0::{item_code}": 0}
			)
		self.assertIn("nothing to transfer", str(raised.exception))

	def test_bundle_component_shortfall_is_reported_against_the_component(self):
		bundle, (component_one, component_two) = self.make_own_bundle([2, 3])
		receive_stock(component_one, 100, self.warehouse, self.company)
		receive_stock(component_two, 9, self.warehouse, self.company)

		transfer_rows = self.expand([(bundle, "Nos", 10)])
		shortfalls = resolve_transfer_shortfalls(transfer_rows, self.company, self.afn_warehouse)[1]

		self.assertEqual([row["item_code"] for row in shortfalls], [component_two])
		self.assertEqual(shortfalls[0]["requested_qty"], 30.0)
		self.assertEqual(shortfalls[0]["available_qty"], 9.0)

	def test_material_request_rows_link_to_their_component_purchase_order(self):
		bundle, (component_one, component_two) = self.make_own_bundle([2, 3])
		make_supplier_price(component_one, self.supplier_one, 1.5)
		make_supplier_price(component_two, self.supplier_two, 2.5)

		transfer_rows = self.expand([(bundle, "Nos", 10)])
		supplier_groups = plan_purchase_orders(transfer_rows)
		material_request, item_map = create_material_request_doc(transfer_rows, self.company, self.warehouse)
		_created, purchase_order_map = submit_purchase_orders(supplier_groups, self.company, self.warehouse)

		for key, material_request_item_names in item_map.items():
			for material_request_item_name in material_request_item_names:
				frappe.db.set_value(
					"Material Request Item",
					material_request_item_name,
					"custom_purchase_order",
					purchase_order_map[key],
				)

		# The FBA Transfer Tracker joins on (purchase order, item code), so every transfer row must
		# point at an order that actually contains that component.
		for row in material_request.reload().items:
			purchase_order_name = frappe.db.get_value(
				"Material Request Item", row.name, "custom_purchase_order"
			)
			self.assertTrue(purchase_order_name)
			self.assertTrue(
				frappe.db.exists(
					"Purchase Order Item", {"parent": purchase_order_name, "item_code": row.item_code}
				)
			)
