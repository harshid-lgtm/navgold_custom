# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint, flt

from navgold_custom.navgold_custom.erpnext_compat import create_delivery_note, make_delivery_note


def on_submit(doc, method):
	auto_reserve_stock(doc)
	auto_create_delivery_notes(doc)


def sort_item_locations_by_walk_route(doc, method):
	doc.locations.sort(key=lambda x: x.custom_walk_route or 0)


def get_delivery_consolidation_warehouse():
	return frappe.db.get_single_value("Navgold Settings", "delivery_consolidation_warehouse")


def get_consolidation_flow_warehouse(pick_list):
	warehouse = get_delivery_consolidation_warehouse()
	# ponytail: bundle picks keep the legacy direct pick-list→DN path (fractional-qty risk
	# only exists for UOM-pack items); extend consolidation to bundles when a bundle SKU
	# actually splits across warehouses
	if warehouse and not has_product_bundle_locations(pick_list):
		return warehouse
	return None


def auto_reserve_stock(doc):
	has_sales_order_items = any(location.sales_order for location in doc.locations)
	if not has_sales_order_items:
		return

	if get_consolidation_flow_warehouse(doc):
		# shelf-level reservations would block the consolidation transfer; the staged
		# stock is reserved against the consolidation warehouse in auto_create_delivery_notes
		return

	try:
		doc.create_stock_reservation_entries(notify=True)
	except Exception:
		frappe.log_error(title=_("Auto Stock Reservation Failed for Pick List {0}").format(doc.name))
		frappe.msgprint(
			_("Stock reservation failed for Pick List {0}. Please reserve stock manually.").format(doc.name),
			indicator="orange",
			alert=True,
		)


def auto_create_delivery_notes(doc):
	has_sales_order_items = any(location.sales_order for location in doc.locations)
	if not has_sales_order_items:
		return

	consolidation_warehouse = get_consolidation_flow_warehouse(doc)
	if consolidation_warehouse:
		consolidate_and_create_delivery_notes(doc, consolidation_warehouse)
		return

	delivery_note = create_delivery_note(doc.name)
	if delivery_note:
		frappe.msgprint(
			_("Delivery Note {0} created automatically").format(delivery_note.name),
			indicator="green",
			alert=True,
		)


def has_product_bundle_locations(pick_list):
	return any(location.product_bundle_item for location in pick_list.locations)


def consolidate_and_create_delivery_notes(pick_list, consolidation_warehouse):
	"""Move picked stock to the consolidation warehouse (in stock UOM, so pack UOMs
	split across warehouses never produce fractional quantities), then create one
	draft Delivery Note per Sales Order sourcing whole packs from there."""
	validate_consolidation_warehouse(pick_list.company, consolidation_warehouse)

	stock_entry = move_picked_stock_to_consolidation(pick_list, consolidation_warehouse)
	if not stock_entry:
		return

	frappe.msgprint(
		_("Picked stock moved to {0} via {1}").format(
			consolidation_warehouse, frappe.utils.get_link_to_form("Stock Entry", stock_entry.name)
		),
		indicator="green",
		alert=True,
	)

	sales_orders = sorted({location.sales_order for location in pick_list.locations if location.sales_order})
	for sales_order_name in sales_orders:
		create_consolidated_delivery_note(pick_list, sales_order_name, consolidation_warehouse)


def validate_consolidation_warehouse(company, warehouse):
	warehouse_details = frappe.db.get_value(
		"Warehouse", warehouse, ["company", "is_group", "warehouse_type", "disabled"], as_dict=True
	)
	if not warehouse_details:
		frappe.throw(_("Delivery Consolidation Warehouse {0} does not exist").format(warehouse))
	if warehouse_details.disabled:
		frappe.throw(_("Delivery Consolidation Warehouse {0} is disabled").format(warehouse))
	if warehouse_details.is_group:
		frappe.throw(
			_("Delivery Consolidation Warehouse {0} must not be a group warehouse").format(warehouse)
		)
	if warehouse_details.company != company:
		frappe.throw(
			_("Delivery Consolidation Warehouse {0} belongs to {1}, not {2}").format(
				warehouse, warehouse_details.company, company
			)
		)
	if warehouse_details.warehouse_type == "Transit":
		frappe.throw(
			_(
				"Delivery Consolidation Warehouse {0} must not be a Transit warehouse:"
				" ERPNext treats transit stock as goods between locations, not deliverable stock"
			).format(warehouse)
		)


def move_picked_stock_to_consolidation(pick_list, consolidation_warehouse):
	if frappe.db.exists("Stock Entry", {"pick_list": pick_list.name, "docstatus": 1}):
		return None

	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = pick_list.company
	stock_entry.purpose = "Material Transfer"
	stock_entry.pick_list = pick_list.name
	stock_entry.set_stock_entry_type()

	bundle_entries_map = get_bundle_entries_map(pick_list)
	for location in pick_list.locations:
		if location.warehouse == consolidation_warehouse:
			continue
		append_transfer_rows_for_location(
			stock_entry,
			location,
			consolidation_warehouse,
			bundle_entries_map.get(location.serial_and_batch_bundle, []),
		)

	if not stock_entry.items:
		return None

	stock_entry.save()
	stock_entry.submit()

	# the stock has physically left the shelves; without this the Open pick list keeps
	# claiming picked-but-undelivered qty there (get_picked_items_details subtracts
	# picked_qty - delivered_qty for every non-Completed pick list) and shorts future
	# pick lists once the shelves are restocked. Rows already at the consolidation
	# warehouse keep their claim — that stock is still awaiting its Delivery Note.
	# ponytail: overloads delivered_qty (the field the shelf claim reads) at stage time, so
	# ERPNext's native "Create Delivery Note" button on this still-Open pick list maps nothing;
	# switch to a custom staged_qty field + a get_picked_items_details override if that native
	# path is ever exposed to users on consolidation pick lists
	for location in pick_list.locations:
		if location.warehouse == consolidation_warehouse or not flt(location.picked_qty):
			continue
		location.db_set("delivered_qty", flt(location.picked_qty), update_modified=False)

	return stock_entry


def get_bundle_entries_map(pick_list):
	bundle_names = [
		location.serial_and_batch_bundle
		for location in pick_list.locations
		if location.serial_and_batch_bundle
	]
	if not bundle_names:
		return {}

	bundle_entries_map = {}
	for entry in frappe.get_all(
		"Serial and Batch Entry",
		filters={"parent": ["in", bundle_names]},
		fields=["parent", "batch_no", "serial_no", "qty"],
	):
		bundle_entries_map.setdefault(entry.parent, []).append(entry)
	return bundle_entries_map


def append_transfer_rows_for_location(stock_entry, location, consolidation_warehouse, bundle_entries):
	base_row = {
		"item_code": location.item_code,
		"s_warehouse": location.warehouse,
		"t_warehouse": consolidation_warehouse,
		"uom": location.stock_uom,
		"stock_uom": location.stock_uom,
		"conversion_factor": 1,
	}

	if location.serial_and_batch_bundle:
		# the pick list's bundle is tied to its own voucher; re-state its batches as
		# plain rows so the transfer builds bundles for its own warehouses
		batch_qty_map = {}
		serial_nos = []
		for entry in bundle_entries:
			if entry.batch_no:
				batch_qty_map[entry.batch_no] = batch_qty_map.get(entry.batch_no, 0) + abs(flt(entry.qty))
			if entry.serial_no:
				serial_nos.append(entry.serial_no)

		if batch_qty_map:
			for batch_no, batch_qty in batch_qty_map.items():
				stock_entry.append(
					"items",
					{**base_row, "qty": batch_qty, "batch_no": batch_no, "use_serial_batch_fields": 1},
				)
		if serial_nos:
			# ponytail: serial+batch combo items land as a serial-only row; split them
			# per batch if a serialised-and-batched item ever enters the pick flow
			stock_entry.append(
				"items",
				{
					**base_row,
					"qty": len(serial_nos),
					"serial_no": "\n".join(serial_nos),
					"use_serial_batch_fields": 1,
				},
			)
		return

	picked_qty = flt(location.picked_qty)
	if not picked_qty:
		return

	stock_entry.append(
		"items",
		{
			**base_row,
			"qty": picked_qty,
			"batch_no": location.batch_no,
			"serial_no": location.serial_no,
			"use_serial_batch_fields": cint(bool(location.batch_no or location.serial_no)),
		},
	)


def get_staged_stock_qty_map(pick_list, sales_order_name):
	"""Staged stock qty (stock UOM) per Sales Order Item for one Sales Order."""
	staged_stock_qty = {}
	for location in pick_list.locations:
		if location.sales_order != sales_order_name or not location.sales_order_item:
			continue
		picked_qty = flt(location.picked_qty)
		if not picked_qty:
			continue
		staged_stock_qty[location.sales_order_item] = (
			staged_stock_qty.get(location.sales_order_item, 0) + picked_qty
		)
	return staged_stock_qty


def create_consolidated_delivery_note(pick_list, sales_order_name, consolidation_warehouse):
	staged_stock_qty = get_staged_stock_qty_map(pick_list, sales_order_name)
	if not staged_stock_qty:
		return

	reserve_staged_stock(pick_list, sales_order_name, consolidation_warehouse, staged_stock_qty)
	reserved_qty_map = get_reserved_qty_map(pick_list.name, sales_order_name)

	kwargs = {"for_reserved_stock": True} if reserved_qty_map else None
	delivery_note = make_delivery_note(sales_order_name, kwargs=kwargs)
	apply_staged_rows(delivery_note, consolidation_warehouse, staged_stock_qty, reserved_qty_map)

	if not delivery_note.get("items"):
		frappe.msgprint(
			_("No whole-pack quantity staged for Sales Order {0}; stock remains in {1}").format(
				sales_order_name, consolidation_warehouse
			),
			indicator="orange",
			alert=True,
		)
		return

	delivery_note.save()
	frappe.msgprint(
		_("Delivery Note {0} created automatically").format(
			frappe.utils.get_link_to_form("Delivery Note", delivery_note.name)
		),
		indicator="green",
		alert=True,
	)


def reserve_staged_stock(pick_list, sales_order_name, consolidation_warehouse, staged_stock_qty):
	"""Best-effort: reserve whole-pack multiples of the staged stock against the
	consolidation warehouse. Whole packs only: a fractional pack cannot be represented
	exactly on the Delivery Note row (reserved_qty / conversion_factor must stay whole).
	Callers must read the outcome via get_reserved_qty_map — reservation can partially
	succeed per item (reserve_stock off, partial reservation disallowed) or throw after
	creating some entries."""
	if not cint(frappe.db.get_single_value("Stock Settings", "enable_stock_reservation")):
		return

	from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
		create_stock_reservation_entries_for_so_items,
	)

	sales_order = frappe.get_doc("Sales Order", sales_order_name)
	conversion_factors = {item.name: flt(item.conversion_factor) or 1 for item in sales_order.items}

	items_details = []
	for sales_order_item, staged_qty in staged_stock_qty.items():
		whole_pack_stock_qty = get_whole_pack_stock_qty(
			staged_qty, conversion_factors.get(sales_order_item, 1)
		)
		if whole_pack_stock_qty <= 0:
			continue
		items_details.append(
			{
				"sales_order_item": sales_order_item,
				"warehouse": consolidation_warehouse,
				"qty_to_reserve": whole_pack_stock_qty,
				"from_voucher_no": pick_list.name,
			}
		)

	if not items_details:
		return

	try:
		# from_voucher_type="Pick List" keeps qty in stock UOM and ties the entries to
		# the pick list so cancelling it cascades; plain SO reservation throws for
		# already-picked items
		create_stock_reservation_entries_for_so_items(
			sales_order, items_details, from_voucher_type="Pick List", notify=True
		)
	except Exception:
		frappe.log_error(
			title=_("Consolidation Stock Reservation Failed for Pick List {0}").format(pick_list.name)
		)


def get_reserved_qty_map(pick_list_name, sales_order_name):
	"""This pick list's active reservations per Sales Order Item. The reserved-stock
	DN mapper pulls every undelivered reservation on the SO — including ones made by
	earlier pick lists — so DN rows must be clamped to this pick list's share."""
	reserved_qty_map = {}
	for entry in frappe.get_all(
		"Stock Reservation Entry",
		filters={"voucher_no": sales_order_name, "from_voucher_no": pick_list_name, "docstatus": 1},
		fields=["voucher_detail_no", "reserved_qty"],
	):
		reserved_qty_map[entry.voucher_detail_no] = reserved_qty_map.get(entry.voucher_detail_no, 0) + flt(
			entry.reserved_qty
		)
	return reserved_qty_map


def whole_pack_count(staged_qty, conversion_factor):
	conversion_factor = flt(conversion_factor) or 1
	# flt(..., 6) before int(): raw float noise (5.999999999) would under-floor a whole pack
	return int(flt(flt(staged_qty) / conversion_factor, 6))


def get_whole_pack_stock_qty(staged_qty, conversion_factor):
	return whole_pack_count(staged_qty, conversion_factor) * (flt(conversion_factor) or 1)


def get_tracked_item_codes(item_codes):
	unique_codes = list({code for code in item_codes if code})
	if not unique_codes:
		return set()
	return set(
		frappe.get_all(
			"Item",
			filters={"name": ["in", unique_codes]},
			or_filters={"has_batch_no": 1, "has_serial_no": 1},
			pluck="name",
		)
	)


def apply_staged_rows(delivery_note, consolidation_warehouse, staged_stock_qty, reserved_qty_map):
	"""Keep only rows backed by THIS pick list's staged stock. Rows born from our
	reservations keep the reservation's exact whole-pack qty, clamped so entries from
	earlier pick lists of the same SO cannot double-ship. Rows without a reservation
	(item skipped, partial reservation refused, reservation disabled) fall back to the
	whole-pack floor from the staged qty. Anything else must not ship on this note."""
	kept_rows = []
	remainder_items = []
	manual_batch_items = []
	remaining_reserved = dict(reserved_qty_map)
	tracked_item_codes = get_tracked_item_codes([row.item_code for row in delivery_note.get("items")])

	for row in delivery_note.get("items"):
		staged_qty = flt(staged_stock_qty.get(row.so_detail))
		if not staged_qty:
			continue

		conversion_factor = flt(row.conversion_factor) or 1

		if row.so_detail not in reserved_qty_map and row.item_code in tracked_item_codes:
			# no reservation means no bundle to source exact batches/serials from; auto-picking a
			# whole pack from the consolidation warehouse would over-draw a single batch and crash
			# on submit. Leave it staged for a manual Delivery Note instead.
			manual_batch_items.append(row.item_code)
			continue

		if row.so_detail in reserved_qty_map:
			# make_delivery_note(for_reserved_stock) pulls EVERY undelivered SRE on the SO. Today
			# that is only ever this pick list's own (all at the consolidation warehouse): ERPNext's
			# validate_sales_order blocks a new pick list while any reservation is undelivered, and
			# refresh_sales_order_reserved_qty keeps that aggregate honest. The warehouse guard is
			# belt-and-suspenders so a foreign reservation could never double-ship on top of our row.
			if row.warehouse != consolidation_warehouse:
				continue
			row_stock_qty = flt(row.qty) * conversion_factor
			allowed_stock_qty = min(row_stock_qty, remaining_reserved.get(row.so_detail, 0))
			if allowed_stock_qty <= 0:
				continue
			remaining_reserved[row.so_detail] -= allowed_stock_qty
			row.qty = allowed_stock_qty / conversion_factor
			kept_rows.append(row)
			continue

		whole_packs = whole_pack_count(staged_qty, conversion_factor)
		if whole_packs <= 0:
			remainder_items.append(row.item_code)
			continue

		row.qty = min(whole_packs, flt(row.qty))
		row.warehouse = consolidation_warehouse
		kept_rows.append(row)

	delivery_note.set("items", kept_rows)
	for index, row in enumerate(delivery_note.get("items")):
		row.idx = index + 1

	if remainder_items:
		frappe.msgprint(
			_("Partial packs for {0} remain in {1} awaiting the balance quantity").format(
				", ".join(sorted(set(remainder_items))), consolidation_warehouse
			),
			indicator="orange",
			alert=True,
		)

	if manual_batch_items:
		frappe.msgprint(
			_(
				"Batch/serial items {0} are staged in {1}; create their Delivery Note manually to pick batches"
			).format(", ".join(sorted(set(manual_batch_items))), consolidation_warehouse),
			indicator="orange",
			alert=True,
		)
