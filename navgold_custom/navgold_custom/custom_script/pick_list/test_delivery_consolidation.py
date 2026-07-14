# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate

from navgold_custom.navgold_custom.erpnext_compat import create_pick_list, make_delivery_note


def get_test_company():
	# local restore carries "Navgold Ltd"; the bare CI site's before_tests creates "Navgold Test"
	for name in ("Navgold Ltd", "Navgold Test"):
		if frappe.db.exists("Company", name):
			return name
	return frappe.db.get_value("Company", {}, "name")


COMPANY = get_test_company()
CUSTOMER = "TEST-DC-CUSTOMER"
PACK_UOM = "Test DC Pack of 6"
PACK_SIZE = 6
BATCH_ITEM_CODE = "TEST-DC-BATCH-ITEM"


def create_test_warehouse(warehouse_name):
	company_abbreviation = frappe.get_cached_value("Company", COMPANY, "abbr")
	full_name = f"{warehouse_name} - {company_abbreviation}"
	if frappe.db.exists("Warehouse", full_name):
		return full_name

	warehouse = frappe.new_doc("Warehouse")
	warehouse.warehouse_name = warehouse_name
	warehouse.company = COMPANY
	warehouse.insert()
	return warehouse.name


def create_pack_uom():
	if not frappe.db.exists("UOM", PACK_UOM):
		uom = frappe.new_doc("UOM")
		uom.uom_name = PACK_UOM
		uom.insert()
	return PACK_UOM


def create_pack_item(item_code, has_batch_no=0):
	if frappe.db.exists("Item", item_code):
		return item_code

	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = item_code
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 1
	if has_batch_no:
		item.has_batch_no = 1
		item.create_new_batch = 1
		item.batch_number_series = "TEST-DC-BATCH-.####"
	item.append("uoms", {"uom": create_pack_uom(), "conversion_factor": PACK_SIZE})
	item.insert()
	return item.name


def create_test_customer():
	if frappe.db.exists("Customer", CUSTOMER):
		return CUSTOMER

	customer = frappe.new_doc("Customer")
	customer.customer_name = CUSTOMER
	customer.customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
	customer.territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
	customer.insert()
	return customer.name


def receive_stock(item_code, warehouse, qty):
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = COMPANY
	stock_entry.purpose = "Material Receipt"
	stock_entry.set_stock_entry_type()
	stock_entry.append(
		"items",
		{
			"item_code": item_code,
			"t_warehouse": warehouse,
			"qty": qty,
			"uom": "Nos",
			"conversion_factor": 1,
			"basic_rate": 10,
		},
	)
	stock_entry.save()
	stock_entry.submit()
	return stock_entry


def get_receipt_batch(stock_entry):
	"""The batch auto-assigned to a create_new_batch receive, read back so a test can assert
	exactly which batch each shelf holds. v15+ carries it on a serial_and_batch_bundle."""
	stock_entry.reload()
	row = stock_entry.items[0]
	if row.batch_no:
		return row.batch_no
	if row.serial_and_batch_bundle:
		batches = frappe.get_all(
			"Serial and Batch Entry", filters={"parent": row.serial_and_batch_bundle}, pluck="batch_no"
		)
		return batches[0] if batches else None
	return None


def get_batch_stock(batch_no, item_code):
	# get_batch_qty returns a per-warehouse list when no warehouse is passed; sum this batch's rows
	# to get its balance across every warehouse
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	rows = get_batch_qty(batch_no=batch_no, item_code=item_code) or []
	return sum(flt(row.get("qty")) for row in rows if row.get("batch_no") == batch_no)


def create_mixed_sales_order(pack_item_code, bundle_item_code, pack_qty=1, bundle_qty=1):
	sales_order = frappe.new_doc("Sales Order")
	sales_order.customer = create_test_customer()
	sales_order.company = COMPANY
	sales_order.delivery_date = nowdate()
	sales_order.append(
		"items",
		{
			"item_code": pack_item_code,
			"qty": pack_qty,
			"uom": PACK_UOM,
			"conversion_factor": PACK_SIZE,
			"rate": 100,
			"reserve_stock": 1,
		},
	)
	sales_order.append(
		"items",
		{"item_code": bundle_item_code, "qty": bundle_qty, "uom": "Nos", "conversion_factor": 1, "rate": 100},
	)
	sales_order.save()
	sales_order.submit()
	return sales_order


def create_bundle_parent_item(item_code):
	if frappe.db.exists("Item", item_code):
		return item_code

	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = item_code
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	# a Product Bundle parent is a non-stock item; only its components hit the stock ledger
	item.is_stock_item = 0
	item.insert()
	return item.name


def create_product_bundle(bundle_item_code, component_item_code, component_qty):
	# built inline (not erpnext's make_product_bundle) so the shared test-record walk never
	# drags in fixtures that trip Navgold's Supplier lead-time validation
	if frappe.db.exists("Product Bundle", {"new_item_code": bundle_item_code}):
		return

	bundle = frappe.new_doc("Product Bundle")
	bundle.new_item_code = bundle_item_code
	bundle.append("items", {"item_code": component_item_code, "qty": component_qty})
	bundle.insert()
	if bundle.meta.is_submittable:
		bundle.submit()


def create_bundle_sales_order(bundle_item_code, qty=1):
	sales_order = frappe.new_doc("Sales Order")
	sales_order.customer = create_test_customer()
	sales_order.company = COMPANY
	sales_order.delivery_date = nowdate()
	sales_order.append(
		"items",
		{"item_code": bundle_item_code, "qty": qty, "uom": "Nos", "conversion_factor": 1, "rate": 100},
	)
	sales_order.save()
	sales_order.submit()
	return sales_order


def get_draft_delivery_note(sales_order):
	names = frappe.get_all(
		"Delivery Note Item",
		filters={"against_sales_order": sales_order.name, "docstatus": 0},
		distinct=True,
		pluck="parent",
	)
	return frappe.get_doc("Delivery Note", names[0]) if names else None


def get_total_stock(item_code):
	return sum(
		flt(bin_row.actual_qty)
		for bin_row in frappe.get_all("Bin", filters={"item_code": item_code}, fields=["actual_qty"])
	)


def create_pack_sales_order(item_code, pack_qty=1):
	sales_order = frappe.new_doc("Sales Order")
	sales_order.customer = create_test_customer()
	sales_order.company = COMPANY
	sales_order.delivery_date = nowdate()
	sales_order.append(
		"items",
		{
			"item_code": item_code,
			"qty": pack_qty,
			"uom": PACK_UOM,
			"conversion_factor": PACK_SIZE,
			"rate": 100,
			"reserve_stock": 1,
		},
	)
	sales_order.save()
	sales_order.submit()
	return sales_order


def submit_pick_list_for(sales_order):
	pick_list = create_pick_list(sales_order.name)
	pick_list.save()
	# the site runs pick lists in scan mode: submit requires every location scanned
	for location in pick_list.locations:
		location.picked_qty = location.stock_qty
	pick_list.save()
	pick_list.submit()
	return pick_list


def submit_pick_list_via_app_flow(sales_order):
	from navgold_custom.navgold_custom.custom_script.pick_list.pick_list_api import (
		map_sales_orders_to_pick_list,
	)

	pick_list = map_sales_orders_to_pick_list(
		frappe.as_json([sales_order.name]), frappe.as_json(frappe.new_doc("Pick List").as_dict())
	)
	pick_list.save()
	for location in pick_list.locations:
		location.picked_qty = location.stock_qty
	pick_list.save()
	pick_list.submit()
	return pick_list


def get_consolidation_transfer(pick_list):
	names = frappe.get_all("Stock Entry", filters={"pick_list": pick_list.name, "docstatus": 1}, pluck="name")
	return frappe.get_doc("Stock Entry", names[0]) if names else None


def get_delivery_note_items(sales_order):
	return frappe.get_all(
		"Delivery Note Item",
		filters={"against_sales_order": sales_order.name, "docstatus": 0},
		fields=["parent", "qty", "stock_qty", "warehouse", "conversion_factor", "uom"],
	)


class TestDeliveryConsolidation(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Create the shared warehouses ONCE per class, not per test. Per-test creation tripped a
		# duplicate-insert on the bare CI site: a mid-test stock-ledger commit persists the warehouse to
		# disk, but the suite's long-lived REPEATABLE READ snapshot never sees it, so frappe.db.exists()
		# reads stale-empty and the next test's insert() collides on the primary key. Creating them here
		# runs the insert exactly once, so it can never collide.
		cls.shelf_a = create_test_warehouse("Test DC Shelf A")
		cls.shelf_b = create_test_warehouse("Test DC Shelf B")
		cls.consolidation_warehouse = create_test_warehouse("Test DC Ready to Ship")
		# The bare CI site ships "Nos" as whole-number-only; the restored local site this suite was
		# written against has it fraction-tolerant. erpnext's legacy (feature-off) pick-list -> DN
		# mapping rounds a pack-UOM conversion (2 Nos -> 0.333 pack -> 1.998 Nos), which errors only
		# under whole-number Nos. Match the local config so the drift doesn't mask/break the mapping test.
		frappe.db.set_value("UOM", "Nos", "must_be_whole_number", 0)

	def setUp(self):
		super().setUp()
		frappe.db.set_single_value(
			"Navgold Settings", "delivery_consolidation_warehouse", self.consolidation_warehouse
		)
		# the restored local site has reservation on; the bare CI site defaults it off. These tests
		# assume it on (the reservation-off cases opt out explicitly via change_settings).
		frappe.db.set_single_value("Stock Settings", "enable_stock_reservation", 1)

	def test_pack_split_across_shelves_delivers_one_whole_pack(self):
		item_code = "TEST-DC-SPLIT-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 2)
		receive_stock(item_code, self.shelf_b, 4)
		sales_order = create_pack_sales_order(item_code)

		pick_list = submit_pick_list_for(sales_order)

		transfer = get_consolidation_transfer(pick_list)
		self.assertIsNotNone(transfer, "consolidation transfer was not created")
		self.assertEqual(sum(flt(row.qty) for row in transfer.items), 6)
		for row in transfer.items:
			self.assertEqual(row.uom, "Nos")
			self.assertEqual(row.t_warehouse, self.consolidation_warehouse)
			self.assertEqual(flt(row.qty) % 1, 0, "transfer must move whole stock units")

		# staging is not delivery: completion is driven by the Delivery Note submit below
		self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Open")

		reservations = frappe.get_all(
			"Stock Reservation Entry",
			filters={"voucher_no": sales_order.name, "docstatus": 1},
			fields=["warehouse", "reserved_qty"],
		)
		self.assertEqual(len(reservations), 1)
		self.assertEqual(reservations[0].warehouse, self.consolidation_warehouse)
		self.assertEqual(flt(reservations[0].reserved_qty), 6)

		delivery_note_items = get_delivery_note_items(sales_order)
		self.assertEqual(len(delivery_note_items), 1, "expected exactly one whole-pack DN row")
		delivery_note_row = delivery_note_items[0]
		self.assertEqual(flt(delivery_note_row.qty), 1)
		self.assertEqual(flt(delivery_note_row.stock_qty), 6)
		self.assertEqual(delivery_note_row.warehouse, self.consolidation_warehouse)
		self.assertEqual(delivery_note_row.uom, PACK_UOM)

		# the original bug killed the flow at submit (whole-number UOM + over-allowance)
		delivery_note = frappe.get_doc("Delivery Note", delivery_note_row.parent)
		delivery_note.submit()
		self.assertEqual(flt(frappe.db.get_value("Sales Order", sales_order.name, "per_delivered")), 100)
		pick_list_state = frappe.db.get_value(
			"Pick List", pick_list.name, ["status", "per_delivered"], as_dict=True
		)
		self.assertEqual(pick_list_state.status, "Completed")
		self.assertEqual(flt(pick_list_state.per_delivered), 100)
		remaining = frappe.db.get_value(
			"Bin",
			{"item_code": item_code, "warehouse": self.consolidation_warehouse},
			"actual_qty",
		)
		self.assertEqual(flt(remaining), 0)

	def test_partial_pick_stages_stock_without_delivery_note(self):
		item_code = "TEST-DC-PARTIAL-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_b, 4)
		sales_order = create_pack_sales_order(item_code)

		pick_list = submit_pick_list_for(sales_order)

		transfer = get_consolidation_transfer(pick_list)
		self.assertIsNotNone(transfer)
		self.assertEqual(sum(flt(row.qty) for row in transfer.items), 4)

		self.assertFalse(
			get_delivery_note_items(sales_order),
			"a partial pack must not produce a Delivery Note (fractional qty)",
		)
		staged = frappe.db.get_value(
			"Bin",
			{"item_code": item_code, "warehouse": self.consolidation_warehouse},
			"actual_qty",
		)
		self.assertEqual(flt(staged), 4, "remainder must wait in the consolidation warehouse")
		self.assertEqual(
			frappe.db.get_value("Pick List", pick_list.name, "status"),
			"Open",
			"nothing delivered yet, the pick list must stay Open",
		)

	def test_feature_off_keeps_legacy_direct_delivery_note(self):
		frappe.db.set_single_value("Navgold Settings", "delivery_consolidation_warehouse", "")
		item_code = "TEST-DC-LEGACY-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 2)
		receive_stock(item_code, self.shelf_b, 4)
		sales_order = create_pack_sales_order(item_code)

		pick_list = submit_pick_list_for(sales_order)

		self.assertIsNone(
			get_consolidation_transfer(pick_list), "no transfer may be created when the feature is off"
		)
		delivery_note_items = get_delivery_note_items(sales_order)
		self.assertEqual(len(delivery_note_items), 2, "legacy path maps one DN row per pick location")
		self.assertEqual({row.warehouse for row in delivery_note_items}, {self.shelf_a, self.shelf_b})
		# the staging-time Completed stamp is gone and the legacy DN is a draft, so the
		# pick list stays Open here; the cascade guard itself is exercised in
		# test_legacy_pick_list_untouched_by_completion_cascade
		self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Open")

	def test_batch_item_transfer_carries_batches(self):
		create_pack_item(BATCH_ITEM_CODE, has_batch_no=1)
		receive_stock(BATCH_ITEM_CODE, self.shelf_a, 2)
		receive_stock(BATCH_ITEM_CODE, self.shelf_b, 4)
		sales_order = create_pack_sales_order(BATCH_ITEM_CODE)

		pick_list = submit_pick_list_for(sales_order)

		transfer = get_consolidation_transfer(pick_list)
		self.assertIsNotNone(transfer)
		self.assertEqual(sum(flt(row.qty) for row in transfer.items), 6)
		for row in transfer.items:
			self.assertEqual(row.t_warehouse, self.consolidation_warehouse)
			has_batch_info = bool(row.batch_no or row.serial_and_batch_bundle or row.use_serial_batch_fields)
			self.assertTrue(has_batch_info, f"transfer row {row.idx} lost its batch information")

		staged = frappe.db.get_value(
			"Bin",
			{"item_code": BATCH_ITEM_CODE, "warehouse": self.consolidation_warehouse},
			"actual_qty",
		)
		self.assertEqual(flt(staged), 6)

		delivery_note_items = get_delivery_note_items(sales_order)
		self.assertEqual(len(delivery_note_items), 1)
		self.assertEqual(flt(delivery_note_items[0].qty), 1)
		self.assertEqual(delivery_note_items[0].warehouse, self.consolidation_warehouse)

	def test_reservation_disabled_floors_to_whole_packs(self):
		item_code = "TEST-DC-NORESV-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 2)
		receive_stock(item_code, self.shelf_b, 4)
		sales_order = create_pack_sales_order(item_code)

		# change_settings restores the singles value cache too — a bare set_single_value
		# leaks the stale cached value into later tests after rollback
		with self.change_settings("Stock Settings", {"enable_stock_reservation": 0}):
			pick_list = submit_pick_list_for(sales_order)

		transfer = get_consolidation_transfer(pick_list)
		self.assertIsNotNone(transfer)
		self.assertFalse(
			frappe.db.exists("Stock Reservation Entry", {"voucher_no": sales_order.name, "docstatus": 1})
		)

		delivery_note_items = get_delivery_note_items(sales_order)
		self.assertEqual(len(delivery_note_items), 1, "fallback path must still produce one whole-pack row")
		delivery_note_row = delivery_note_items[0]
		self.assertEqual(flt(delivery_note_row.qty), 1)
		self.assertEqual(flt(delivery_note_row.stock_qty), 6)
		self.assertEqual(delivery_note_row.warehouse, self.consolidation_warehouse)

	def test_second_pick_list_ships_only_its_own_reservation(self):
		item_code = "TEST-DC-SEQ-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 6)
		sales_order = create_pack_sales_order(item_code, pack_qty=2)

		first_pick_list = submit_pick_list_for(sales_order)
		first_delivery_items = get_delivery_note_items(sales_order)
		self.assertEqual(len(first_delivery_items), 1)
		self.assertEqual(flt(first_delivery_items[0].qty), 1)
		frappe.get_doc("Delivery Note", first_delivery_items[0].parent).submit()

		# SO-level delivery_status gates completion: half-delivered SO keeps every pick list Open
		self.assertEqual(frappe.db.get_value("Pick List", first_pick_list.name, "status"), "Open")

		receive_stock(item_code, self.shelf_b, 6)
		second_pick_list = submit_pick_list_for(sales_order)
		self.assertNotEqual(first_pick_list.name, second_pick_list.name)

		second_delivery_items = get_delivery_note_items(sales_order)
		self.assertEqual(len(second_delivery_items), 1)
		self.assertEqual(
			flt(second_delivery_items[0].qty),
			1,
			"second DN must ship only the second pick list's pack, not every reservation on the SO",
		)

		frappe.get_doc("Delivery Note", second_delivery_items[0].parent).submit()
		self.assertEqual(flt(frappe.db.get_value("Sales Order", sales_order.name, "per_delivered")), 100)
		self.assertEqual(frappe.db.get_value("Pick List", first_pick_list.name, "status"), "Completed")
		self.assertEqual(frappe.db.get_value("Pick List", second_pick_list.name, "status"), "Completed")

	def test_delivery_note_cancel_reopens_pick_list(self):
		item_code = "TEST-DC-CANCEL-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 2)
		receive_stock(item_code, self.shelf_b, 4)
		sales_order = create_pack_sales_order(item_code)

		pick_list = submit_pick_list_for(sales_order)
		delivery_note_items = get_delivery_note_items(sales_order)
		self.assertEqual(len(delivery_note_items), 1)
		delivery_note = frappe.get_doc("Delivery Note", delivery_note_items[0].parent)
		delivery_note.submit()
		self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Completed")

		delivery_note.reload()
		delivery_note.cancel()

		pick_list_state = frappe.db.get_value(
			"Pick List", pick_list.name, ["status", "per_delivered"], as_dict=True
		)
		self.assertEqual(pick_list_state.status, "Open", "cancelled delivery must reopen the pick list")
		self.assertEqual(flt(pick_list_state.per_delivered), 0)

	def test_multi_sales_order_pick_list_completes_only_when_all_ship(self):
		item_code = "TEST-DC-MULTISO-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 12)
		first_sales_order = create_pack_sales_order(item_code)
		second_sales_order = create_pack_sales_order(item_code)

		pick_list = create_pick_list(first_sales_order.name)
		pick_list = create_pick_list(second_sales_order.name, target_doc=pick_list)
		pick_list.save()
		for location in pick_list.locations:
			location.picked_qty = location.stock_qty
		pick_list.save()
		pick_list.submit()

		first_delivery_items = get_delivery_note_items(first_sales_order)
		second_delivery_items = get_delivery_note_items(second_sales_order)
		self.assertEqual(len(first_delivery_items), 1)
		self.assertEqual(len(second_delivery_items), 1)

		frappe.get_doc("Delivery Note", first_delivery_items[0].parent).submit()
		self.assertEqual(
			frappe.db.get_value("Pick List", pick_list.name, "status"),
			"Open",
			"one undelivered sales order must keep the multi-SO pick list Open",
		)

		frappe.get_doc("Delivery Note", second_delivery_items[0].parent).submit()
		self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Completed")

	def test_legacy_pick_list_untouched_by_completion_cascade(self):
		item_code = "TEST-DC-LEGACY-GUARD-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 6)
		with self.change_settings("Navgold Settings", {"delivery_consolidation_warehouse": ""}):
			sales_order = create_pack_sales_order(item_code)
			pick_list = submit_pick_list_for(sales_order)

		# deliver the SO with an SO-direct DN while the feature is on: the cascade runs,
		# finds this pick list as a candidate, and must skip it (no consolidation transfer)
		delivery_note = make_delivery_note(sales_order.name)
		for row in delivery_note.items:
			row.warehouse = self.shelf_a
		delivery_note.save()
		delivery_note.submit()

		self.assertEqual(
			frappe.db.get_value("Sales Order", sales_order.name, "delivery_status"), "Fully Delivered"
		)
		self.assertEqual(
			frappe.db.get_value("Pick List", pick_list.name, "status"),
			"Open",
			"the cascade must never stamp a legacy pick list Completed",
		)

	def test_reservation_disabled_batch_item_stays_staged(self):
		# Reservation off + batch item: without a reservation there is no bundle to source exact
		# batches whole-pack from the consolidation warehouse, so auto-picking would over-draw a
		# single batch and crash on submit. The flow must leave the stock staged for a manual
		# Delivery Note instead — never auto-create a bundle-less row.
		item_code = "TEST-DC-NORESV-BATCH-ITEM"
		create_pack_item(item_code, has_batch_no=1)
		receive_stock(item_code, self.shelf_a, 2)
		receive_stock(item_code, self.shelf_b, 4)
		sales_order = create_pack_sales_order(item_code)

		with self.change_settings("Stock Settings", {"enable_stock_reservation": 0}):
			pick_list = submit_pick_list_for(sales_order)

		transfer = get_consolidation_transfer(pick_list)
		self.assertIsNotNone(transfer)
		self.assertFalse(
			frappe.db.exists("Stock Reservation Entry", {"voucher_no": sales_order.name, "docstatus": 1})
		)
		self.assertFalse(
			get_delivery_note_items(sales_order),
			"a batch item with no reservation must not auto-create a bundle-less DN",
		)
		staged = frappe.db.get_value(
			"Bin",
			{"item_code": item_code, "warehouse": self.consolidation_warehouse},
			"actual_qty",
		)
		self.assertEqual(flt(staged), 6, "the batch stock must wait in the consolidation warehouse")
		self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Open")

	def test_bundle_split_across_shelves_delivers_from_consolidation(self):
		# a bundle component picked across TWO shelves crashed ERPNext's native pick-list→DN
		# mapper: it collapsed the split into ONE packed_items row at ONE shelf with the FULL
		# qty (6 units all on Shelf B, which only holds 4) → NegativeStockError on submit.
		# Consolidation routes the component into one warehouse first, so the single-warehouse
		# packed_items row becomes valid.
		component_code = "TEST-DC-BUNDLE-COMPONENT"
		bundle_code = "TEST-DC-BUNDLE-PARENT"
		create_pack_item(component_code)
		create_bundle_parent_item(bundle_code)
		create_product_bundle(bundle_code, component_code, PACK_SIZE)
		receive_stock(component_code, self.shelf_a, 2)
		receive_stock(component_code, self.shelf_b, 4)
		sales_order = create_bundle_sales_order(bundle_code, qty=1)

		pick_list = submit_pick_list_for(sales_order)

		transfer = get_consolidation_transfer(pick_list)
		self.assertIsNotNone(
			transfer, "split-bundle pick must stage its component via a consolidation transfer"
		)
		self.assertEqual(sum(flt(row.qty) for row in transfer.items), 6)
		for row in transfer.items:
			self.assertEqual(row.t_warehouse, self.consolidation_warehouse)

		delivery_note = get_draft_delivery_note(sales_order)
		self.assertIsNotNone(delivery_note, "a draft Delivery Note must be created for the split bundle")
		for packed_item in delivery_note.packed_items:
			self.assertEqual(
				packed_item.warehouse,
				self.consolidation_warehouse,
				"every packed component must source from the single staged warehouse",
			)

		# the original bug killed the flow here (full qty against a shelf that can't cover it)
		delivery_note.submit()

		self.assertEqual(
			get_total_stock(component_code), 0, "the whole bundle component must ship out to zero stock"
		)
		self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Completed")

	def test_bundle_split_via_app_map_flow_delivers_from_consolidation(self):
		component_code = "TEST-DC-APPFLOW-COMPONENT"
		bundle_code = "TEST-DC-APPFLOW-PARENT"
		create_pack_item(component_code)
		create_bundle_parent_item(bundle_code)
		create_product_bundle(bundle_code, component_code, PACK_SIZE)
		receive_stock(component_code, self.shelf_a, 2)
		receive_stock(component_code, self.shelf_b, 4)
		sales_order = create_bundle_sales_order(bundle_code, qty=1)

		pick_list = submit_pick_list_via_app_flow(sales_order)

		transfer = get_consolidation_transfer(pick_list)
		self.assertIsNotNone(
			transfer, "app-flow split-bundle pick must stage its component via a consolidation transfer"
		)
		self.assertEqual(sum(flt(row.qty) for row in transfer.items), 6)
		for row in transfer.items:
			self.assertEqual(row.t_warehouse, self.consolidation_warehouse)

		delivery_note = get_draft_delivery_note(sales_order)
		self.assertIsNotNone(delivery_note, "app flow must create a draft Delivery Note for the split bundle")
		for packed_item in delivery_note.packed_items:
			self.assertEqual(packed_item.warehouse, self.consolidation_warehouse)

		delivery_note.submit()
		self.assertEqual(get_total_stock(component_code), 0)
		self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Completed")

	def test_batch_pack_split_delivers_both_batches(self):
		# a batched pack of 6 = 2 of batch X (shelf A) + 4 of batch Y (shelf B). Reservation ON (site
		# default), so the DN is built from this pick list's own reservation at the consolidation
		# warehouse. It must submit cleanly and DRAW BOTH batches (2 from X, 4 from Y), not just move
		# them — the existing transfer test never proved the delivery half.
		item_code = "TEST-DC-BATCHPACK-ITEM"
		create_pack_item(item_code, has_batch_no=1)
		receipt_x = receive_stock(item_code, self.shelf_a, 2)
		receipt_y = receive_stock(item_code, self.shelf_b, 4)
		batch_x = get_receipt_batch(receipt_x)
		batch_y = get_receipt_batch(receipt_y)
		self.assertTrue(batch_x and batch_y and batch_x != batch_y, "expected two distinct batches")
		self.assertEqual(get_batch_stock(batch_x, item_code), 2)
		self.assertEqual(get_batch_stock(batch_y, item_code), 4)

		sales_order = create_pack_sales_order(item_code)
		# the batch-wise reservation path only engages with both settings on; pin them so a bare CI
		# site (use_serial_batch_fields off) can't quietly route through the mapper and prove nothing
		with self.change_settings(
			"Stock Settings", {"use_serial_batch_fields": 1, "enable_stock_reservation": 1}
		):
			pick_list = submit_pick_list_for(sales_order)

			delivery_note_items = get_delivery_note_items(sales_order)
			self.assertEqual(len(delivery_note_items), 1, "expected one whole-pack DN row")
			delivery_note = frappe.get_doc("Delivery Note", delivery_note_items[0].parent)
			delivery_note.submit()

			self.assertEqual(get_batch_stock(batch_x, item_code), 0, "batch X (2 units) must ship out")
			self.assertEqual(get_batch_stock(batch_y, item_code), 0, "batch Y (4 units) must ship out")
			self.assertEqual(get_total_stock(item_code), 0, "no stock may remain anywhere after delivery")
			self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Completed")

	def test_batch_bundle_component_split_delivers_both_batches(self):
		# same proof for a batched bundle COMPONENT scattered across two shelves: 2 of batch X + 4 of
		# batch Y build one whole bundle. The split-bundle DN (no reservation) must submit and consume
		# both batches to zero, not strand either.
		component_code = "TEST-DC-BATCH-BUNDLE-COMPONENT"
		bundle_code = "TEST-DC-BATCH-BUNDLE-PARENT"
		create_pack_item(component_code, has_batch_no=1)
		create_bundle_parent_item(bundle_code)
		create_product_bundle(bundle_code, component_code, PACK_SIZE)
		receipt_x = receive_stock(component_code, self.shelf_a, 2)
		receipt_y = receive_stock(component_code, self.shelf_b, 4)
		batch_x = get_receipt_batch(receipt_x)
		batch_y = get_receipt_batch(receipt_y)
		self.assertTrue(batch_x and batch_y and batch_x != batch_y, "expected two distinct batches")

		sales_order = create_bundle_sales_order(bundle_code, qty=1)
		with self.change_settings(
			"Stock Settings", {"use_serial_batch_fields": 1, "enable_stock_reservation": 1}
		):
			pick_list = submit_pick_list_for(sales_order)

			delivery_note = get_draft_delivery_note(sales_order)
			self.assertIsNotNone(delivery_note, "a draft Delivery Note must be created for the split bundle")
			delivery_note.submit()

			self.assertEqual(get_batch_stock(batch_x, component_code), 0, "batch X (2 units) must ship out")
			self.assertEqual(get_batch_stock(batch_y, component_code), 0, "batch Y (4 units) must ship out")
			self.assertEqual(
				get_total_stock(component_code), 0, "the whole bundle component must ship to zero"
			)
			self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Completed")

	def test_mixed_sales_order_delivers_pack_and_bundle_in_one_note(self):
		# ONE Sales Order carrying BOTH a scattered standalone pack line AND a split-component bundle
		# line. The old all-or-nothing routing ran the bundle-only builder for the whole SO and DROPPED
		# the standalone pack (its stock stranded in the consolidation warehouse). The per-SO mixed
		# builder must keep both lines on ONE Delivery Note that submits and ships both to zero stock.
		pack_item = "TEST-DC-MIXED-PACK-ITEM"
		component_code = "TEST-DC-MIXED-BUNDLE-COMPONENT"
		bundle_code = "TEST-DC-MIXED-BUNDLE-PARENT"
		create_pack_item(pack_item)
		create_pack_item(component_code)
		create_bundle_parent_item(bundle_code)
		create_product_bundle(bundle_code, component_code, PACK_SIZE)
		# both lines scattered across the two shelves so both exercise consolidation
		receive_stock(pack_item, self.shelf_a, 2)
		receive_stock(pack_item, self.shelf_b, 4)
		receive_stock(component_code, self.shelf_a, 2)
		receive_stock(component_code, self.shelf_b, 4)

		sales_order = create_mixed_sales_order(pack_item, bundle_code)
		pick_list = submit_pick_list_for(sales_order)

		delivery_note_items = get_delivery_note_items(sales_order)
		delivery_note_names = {row.parent for row in delivery_note_items}
		self.assertEqual(len(delivery_note_names), 1, "the mixed SO must produce exactly one Delivery Note")
		delivery_note = frappe.get_doc("Delivery Note", delivery_note_names.pop())

		item_codes = {row.item_code for row in delivery_note.items}
		self.assertIn(pack_item, item_codes, "the standalone pack line must not be dropped")
		self.assertIn(bundle_code, item_codes, "the bundle parent line must be delivered")
		for packed_item in delivery_note.packed_items:
			self.assertEqual(
				packed_item.warehouse,
				self.consolidation_warehouse,
				"every bundle component must source from the single staged warehouse",
			)

		# the standalone pack line is reserved (so a batched pack can ship its exact batches); the
		# bundle parent is a non-stock item and is never reserved
		self.assertTrue(
			frappe.db.exists(
				"Stock Reservation Entry",
				{"voucher_no": sales_order.name, "item_code": pack_item, "docstatus": 1},
			),
			"the standalone pack line must be reserved",
		)

		delivery_note.submit()

		self.assertEqual(get_total_stock(pack_item), 0, "the standalone pack must ship to zero stock")
		self.assertEqual(get_total_stock(component_code), 0, "the bundle component must ship to zero stock")
		self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Completed")

	def test_mixed_sales_order_via_app_flow_keeps_standalone_pack(self):
		from navgold_custom.navgold_custom.custom_script.pick_list.pick_list_api import (
			map_sales_orders_to_pick_list,
		)

		pack_item = "TEST-DC-APPMIX-PACK"
		component_code = "TEST-DC-APPMIX-COMPONENT"
		bundle_code = "TEST-DC-APPMIX-BUNDLE"
		create_pack_item(pack_item)
		create_pack_item(component_code)
		create_bundle_parent_item(bundle_code)
		create_product_bundle(bundle_code, component_code, PACK_SIZE)
		receive_stock(pack_item, self.shelf_a, 2)
		receive_stock(pack_item, self.shelf_b, 4)
		receive_stock(component_code, self.shelf_a, 2)
		receive_stock(component_code, self.shelf_b, 4)
		sales_order = create_mixed_sales_order(pack_item, bundle_code)

		pick_list = map_sales_orders_to_pick_list(
			frappe.as_json([sales_order.name]), frappe.as_json(frappe.new_doc("Pick List").as_dict())
		)

		location_item_codes = {location.item_code for location in pick_list.locations}
		self.assertIn(
			pack_item, location_item_codes, "app flow dropped the standalone pack line from the pick list"
		)
		self.assertIn(component_code, location_item_codes, "bundle component missing from the pick list")

	def test_mixed_sales_order_with_batched_pack_delivers_both(self):
		# the gap found on live data: a mixed SO whose standalone pack is BATCHED (2 of batch X + 4 of
		# batch Y, scattered) alongside a split bundle. Without reserving the pack line, the batched row
		# hit the "create DN manually" guard and stranded. The mixed builder must reserve the standalone
		# line batch-wise so both the batched pack and the bundle deliver on one note that submits clean.
		pack_item = "TEST-DC-MIXBATCH-PACK-ITEM"
		component_code = "TEST-DC-MIXBATCH-BUNDLE-COMPONENT"
		bundle_code = "TEST-DC-MIXBATCH-BUNDLE-PARENT"
		create_pack_item(pack_item, has_batch_no=1)
		create_pack_item(component_code)
		create_bundle_parent_item(bundle_code)
		create_product_bundle(bundle_code, component_code, PACK_SIZE)
		receipt_x = receive_stock(pack_item, self.shelf_a, 2)
		receipt_y = receive_stock(pack_item, self.shelf_b, 4)
		batch_x = get_receipt_batch(receipt_x)
		batch_y = get_receipt_batch(receipt_y)
		self.assertTrue(batch_x and batch_y and batch_x != batch_y, "expected two distinct batches")
		receive_stock(component_code, self.shelf_a, 2)
		receive_stock(component_code, self.shelf_b, 4)

		sales_order = create_mixed_sales_order(pack_item, bundle_code)
		with self.change_settings(
			"Stock Settings", {"use_serial_batch_fields": 1, "enable_stock_reservation": 1}
		):
			pick_list = submit_pick_list_for(sales_order)

			delivery_note_names = {row.parent for row in get_delivery_note_items(sales_order)}
			self.assertEqual(
				len(delivery_note_names), 1, "the mixed SO must produce exactly one Delivery Note"
			)
			delivery_note = frappe.get_doc("Delivery Note", delivery_note_names.pop())
			item_codes = {row.item_code for row in delivery_note.items}
			self.assertIn(pack_item, item_codes, "the batched standalone pack must not be stranded")
			self.assertIn(bundle_code, item_codes, "the bundle parent line must be delivered")

			delivery_note.submit()

		self.assertEqual(get_batch_stock(batch_x, pack_item), 0, "batch X (2 units) must ship out")
		self.assertEqual(get_batch_stock(batch_y, pack_item), 0, "batch Y (4 units) must ship out")
		self.assertEqual(get_total_stock(pack_item), 0, "the batched pack must ship to zero stock")
		self.assertEqual(get_total_stock(component_code), 0, "the bundle component must ship to zero stock")
		self.assertEqual(frappe.db.get_value("Pick List", pick_list.name, "status"), "Completed")

	def test_restocked_shelf_is_pickable_while_staged(self):
		item_code = "TEST-DC-RESTOCK-ITEM"
		create_pack_item(item_code)
		receive_stock(item_code, self.shelf_a, 6)
		first_sales_order = create_pack_sales_order(item_code)
		submit_pick_list_for(first_sales_order)

		# first pick is staged (DN still draft, pick list still Open); a restock on the
		# same shelf must be fully pickable — the staged pick may not claim shelf stock
		receive_stock(item_code, self.shelf_a, 6)
		second_sales_order = create_pack_sales_order(item_code)
		second_pick_list = submit_pick_list_for(second_sales_order)

		self.assertEqual(
			sum(flt(location.picked_qty) for location in second_pick_list.locations),
			6,
			"staged pick list still claims the restocked shelf stock",
		)
