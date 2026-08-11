# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from erpnext.stock.doctype.pick_list.pick_list import get_rejected_warehouses
from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
	create_stock_reservation_entries_for_so_items,
)
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Sum
from frappe.utils import cint, flt
from frappe.utils.caching import redis_cache
from frappe.utils.nestedset import get_descendants_of

from navgold_custom.navgold_custom.erpnext_compat import (
	create_dn_with_so,
	create_dn_wo_so,
	get_product_bundle_items,
	get_reserved_batch_bundle_name,
	make_delivery_note,
	validate_item_locations,
)


def on_submit(doc, method):
	auto_reserve_stock(doc)
	auto_create_delivery_notes(doc)


def block_partial_pick(doc, method=None):
	# hook entry: runs after erpnext's before_save has allocated the locations
	drop_sales_orders_short_of_stock(doc)


def snap_locations_to_whole_stock_units(doc, method=None):
	whole_number_uoms = get_whole_number_uoms()
	if not whole_number_uoms:
		return

	qty_precision = frappe.get_precision("Pick List Item", "qty")
	for row in doc.get("locations"):
		if row.stock_uom not in whole_number_uoms:
			continue
		conversion_factor = flt(row.conversion_factor) or 1
		rounding_dust = conversion_factor * 0.5 * 10**-qty_precision
		row.stock_qty = snap_to_whole_units(row.stock_qty, rounding_dust)
		row.picked_qty = snap_to_whole_units(row.picked_qty, rounding_dust)
		row.qty = flt(row.stock_qty) / conversion_factor


def snap_to_whole_units(value, rounding_dust):
	value = flt(value)
	nearest_whole = flt(value, 0)
	if abs(value - nearest_whole) <= rounding_dust:
		return nearest_whole
	return cint(value)


@redis_cache()
def get_whole_number_uoms():
	return set(frappe.get_all("UOM", filters={"must_be_whole_number": 1}, pluck="name"))


def set_part_pack_rows_to_stock_uom(stock_entry, pick_list_doc):
	pending_stock_qty = {
		row.name: flt(row.picked_qty) - flt(row.transferred_qty) for row in pick_list_doc.locations
	}
	precision = frappe.get_precision("Stock Entry Detail", "transfer_qty")

	for row in stock_entry.get("items") or []:
		pending_qty = pending_stock_qty.get(row.get("pick_list_item"))
		if not pending_qty:
			continue

		conversion_factor = flt(row.get("conversion_factor")) or 1
		if flt(flt(row.get("qty")) * conversion_factor, precision) == flt(pending_qty, precision):
			continue

		row["qty"] = pending_qty
		row["transfer_qty"] = pending_qty
		row["uom"] = row.get("stock_uom")
		row["conversion_factor"] = 1


def drop_sales_orders_short_of_stock(pick_list, requested_sales_orders=None):
	"""A Sales Order is picked whole or not at all, however many lines it has. An order the shelves
	cannot cover is dropped so the orders that are fully in stock can still be picked, and nothing left
	to pick means no Pick List at all. Pass requested_sales_orders to name the orders that were asked
	for, so an order the allocation stripped of every row is reported instead of quietly disappearing.
	Tick Pick Manually to keep a deliberate short pick."""
	if pick_list.docstatus > 0 or pick_list.purpose != "Delivery" or pick_list.pick_manually:
		return

	shortages = get_pick_shortages(pick_list)
	remove_short_sales_orders(pick_list, shortages)

	messages = [line for lines in shortages.values() for line in lines]
	picked_sales_orders = {row.sales_order for row in pick_list.locations if row.sales_order}
	for sales_order in requested_sales_orders or []:
		if sales_order not in picked_sales_orders and sales_order not in shortages:
			messages.append(_("{0}: nothing available to pick").format(sales_order))

	if not pick_list.get("locations"):
		if messages:
			reason = (
				_("No Sales Order can be picked in full, so no Pick List was created:")
				+ "<br><br>"
				+ "<br>".join(messages)
			)
		else:
			reason = _("No stock is available to pick for this order.")

		frappe.throw(reason, title=_("Insufficient Stock"))

	if messages:
		frappe.msgprint(
			_("These Sales Orders are not fully in stock and were left out of this Pick List:")
			+ "<br><br>"
			+ "<br>".join(messages),
			title=_("Insufficient Stock"),
			indicator="orange",
		)


def get_pick_shortages(pick_list):
	"""Shortfall lines of every Sales Order the allocation could not cover in full, keyed by order."""
	sales_orders = {row.sales_order for row in pick_list.locations if row.sales_order}
	if not sales_orders:
		return {}

	allocated_stock_qty = {}
	for row in pick_list.locations:
		demand_row = row.product_bundle_item or row.sales_order_item
		if demand_row:
			allocated_stock_qty[demand_row] = allocated_stock_qty.get(demand_row, 0) + flt(row.stock_qty)

	precision = frappe.get_precision("Pick List Item", "stock_qty")
	shortages = {}
	for demand in get_ordered_stock_qty(sales_orders):
		short_qty = flt(demand.stock_qty - allocated_stock_qty.get(demand.row_name, 0), precision)
		if short_qty > 0:
			shortages.setdefault(demand.sales_order, []).append(
				_("{0} for {1}: {2} {3} short").format(
					frappe.bold(demand.item_code),
					demand.sales_order,
					short_qty,
					demand.stock_uom,
				)
			)

	return shortages


def remove_short_sales_orders(pick_list, shortages):
	# a row already scanned into a tote stays: that stock is physically off the shelf already
	short_rows = [
		row for row in pick_list.locations if row.sales_order in shortages and not flt(row.picked_qty)
	]
	for row in short_rows:
		pick_list.remove(row)

	for idx, row in enumerate(pick_list.locations, start=1):
		row.idx = idx


def get_ordered_stock_qty(sales_orders):
	"""Yet-to-be-picked stock qty of every line the pick list is expected to cover, sized the same way
	erpnext's Sales Order -> Pick List mapper sizes its rows. Read back from the Sales Orders rather
	than the pick list because a line with no stock at all is dropped from the pick list entirely."""
	sales_orders = list(sales_orders)
	sales_order_items = frappe.get_all(
		"Sales Order Item",
		filters={"parent": ("in", sales_orders), "docstatus": 1},
		fields=[
			"name",
			"parent",
			"item_code",
			"stock_uom",
			"qty",
			"picked_qty",
			"delivered_qty",
			"conversion_factor",
			"delivered_by_supplier",
		],
	)
	packed_items = frappe.get_all(
		"Packed Item",
		filters={"parent": ("in", sales_orders), "docstatus": 1},
		fields=["name", "parent", "parent_detail_docname", "item_code", "uom", "qty"],
	)
	bundle_rows = {row.parent_detail_docname for row in packed_items}

	demand = []
	pending_fraction = {}
	for item in sales_order_items:
		picked_qty = flt(item.picked_qty) / (flt(item.conversion_factor) or 1)
		pending_qty = flt(item.qty) - max(picked_qty, flt(item.delivered_qty))
		pending_fraction[item.name] = pending_qty / flt(item.qty) if item.qty else 0

		if item.delivered_by_supplier or item.name in bundle_rows or pending_qty <= 0:
			continue

		demand.append(
			frappe._dict(
				row_name=item.name,
				sales_order=item.parent,
				item_code=item.item_code,
				stock_uom=item.stock_uom,
				stock_qty=pending_qty * flt(item.conversion_factor),
			)
		)

	for packed_item in packed_items:
		pending_qty = flt(packed_item.qty) * pending_fraction.get(packed_item.parent_detail_docname, 0)
		if pending_qty <= 0:
			continue

		demand.append(
			frappe._dict(
				row_name=packed_item.name,
				sales_order=packed_item.parent,
				item_code=packed_item.item_code,
				stock_uom=packed_item.uom,
				stock_qty=pending_qty,
			)
		)

	# non-stock lines (services, non-bundle non-stock items) never get a pick location
	return [row for row in demand if is_pickable(row.item_code)]


def is_pickable(item_code):
	return cint(frappe.get_cached_value("Item", item_code, "is_stock_item")) or bool(
		get_product_bundle_items(item_code)
	)


def get_stock_fulfillment(sales_orders, company, parent_warehouse=None):
	demand = get_demand_by_sales_order(sales_orders)
	available = get_pickable_stock_qty(
		{item_code for lines in demand.values() for item_code in lines}, company, parent_warehouse
	)

	precision = frappe.get_precision("Pick List Item", "stock_qty")
	fulfillment = {}
	for sales_order in sales_orders:
		lines = demand.get(sales_order)
		in_stock = bool(lines) and all(
			flt(available.get(item_code, 0) - stock_qty, precision) >= 0
			for item_code, stock_qty in lines.items()
		)
		fulfillment[sales_order] = in_stock

		if in_stock:
			for item_code, stock_qty in lines.items():
				available[item_code] -= stock_qty

	return fulfillment


def get_demand_by_sales_order(sales_orders):
	demand = {}
	for row in get_ordered_stock_qty(sales_orders):
		lines = demand.setdefault(row.sales_order, {})
		lines[row.item_code] = lines.get(row.item_code, 0) + row.stock_qty
	return demand


def get_pickable_stock_qty(item_codes, company, parent_warehouse=None, exclude_warehouses=None):
	if not item_codes:
		return {}

	warehouses = get_pickable_warehouses(company, parent_warehouse, exclude_warehouses)
	if not warehouses:
		return {}

	item_codes = list(item_codes)
	bin_table = frappe.qb.DocType("Bin")
	shelf_qty = (
		frappe.qb.from_(bin_table)
		.select(bin_table.item_code, Sum(bin_table.actual_qty).as_("qty"))
		.where(bin_table.item_code.isin(item_codes) & bin_table.warehouse.isin(warehouses))
		.groupby(bin_table.item_code)
	).run(as_dict=True)
	available = {row.item_code: flt(row.qty) for row in shelf_qty}

	for item_code, claimed_qty in get_open_pick_list_claims(item_codes, warehouses).items():
		available[item_code] = available.get(item_code, 0) - claimed_qty

	return available


def get_pickable_warehouses(company, parent_warehouse=None, exclude_warehouses=None):
	if parent_warehouse:
		warehouses = [parent_warehouse, *get_descendants_of("Warehouse", parent_warehouse)]
	else:
		warehouses = frappe.get_all("Warehouse", filters={"company": company, "is_group": 0}, pluck="name")

	skipped_warehouses = set(get_rejected_warehouses()) | set(exclude_warehouses or [])
	return [warehouse for warehouse in warehouses if warehouse not in skipped_warehouses]


def get_open_pick_list_claims(item_codes, warehouses):
	pick_list = frappe.qb.DocType("Pick List")
	pick_list_item = frappe.qb.DocType("Pick List Item")
	settled_qty = (
		Case()
		.when(pick_list.purpose == "Delivery", pick_list_item.delivered_qty)
		.else_(pick_list_item.transferred_qty)
	)
	claimed_qty = (
		Case()
		.when(
			(pick_list_item.picked_qty > 0) & (pick_list_item.docstatus == 1),
			pick_list_item.picked_qty - settled_qty,
		)
		.else_(pick_list_item.stock_qty)
	)

	claims = (
		frappe.qb.from_(pick_list)
		.inner_join(pick_list_item)
		.on(pick_list.name == pick_list_item.parent)
		.select(pick_list_item.item_code, Sum(claimed_qty).as_("qty"))
		.where(
			pick_list_item.item_code.isin(item_codes)
			& pick_list_item.warehouse.isin(warehouses)
			& pick_list.status.notin(["Completed", "Cancelled"])
			& (pick_list_item.docstatus != 2)
			& ((pick_list_item.picked_qty > 0) | (pick_list_item.stock_qty > 0))
		)
		.groupby(pick_list_item.item_code)
	).run(as_dict=True)

	return {row.item_code: flt(row.qty) for row in claims}


def sort_item_locations_by_walk_route(doc, method):
	warehouses = {row.warehouse for row in doc.locations if row.warehouse}
	if not warehouses:
		return

	walk_route_map = frappe._dict(
		frappe.get_all(
			"Warehouse",
			filters={"name": ("in", warehouses)},
			fields=["name", "custom_walk_route"],
			as_list=1,
		)
	)

	for row in doc.locations:
		row.custom_walk_route = flt(walk_route_map.get(row.warehouse))

	doc.locations.sort(key=lambda row: row.custom_walk_route or float("inf"))
	for idx, row in enumerate(doc.locations, start=1):
		row.idx = idx


def get_delivery_consolidation_warehouse():
	return frappe.db.get_single_value("Navgold Settings", "delivery_consolidation_warehouse")


def get_consolidation_flow_warehouse(pick_list):
	warehouse = get_delivery_consolidation_warehouse()
	if not warehouse:
		return None
	if has_part_pack_standalone_locations(pick_list):
		return warehouse
	if not has_product_bundle_locations(pick_list):
		return warehouse
	if has_split_product_bundle_locations(pick_list):
		return warehouse
	return None


def has_part_pack_standalone_locations(pick_list):
	for location in pick_list.locations:
		if location.product_bundle_item or not location.sales_order_item:
			continue
		picked_qty = flt(location.picked_qty)
		conversion_factor = flt(location.conversion_factor) or 1
		if not picked_qty or conversion_factor <= 1:
			continue
		packs = flt(picked_qty / conversion_factor, 6)
		if packs != int(packs):
			return True
	return False


def auto_reserve_stock(doc):
	has_sales_order_items = any(location.sales_order for location in doc.locations)
	if not has_sales_order_items:
		return

	if get_consolidation_flow_warehouse(doc):
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

	delivery_notes = create_delivery_notes_per_sales_order(doc)
	if delivery_notes:
		frappe.msgprint(
			_("Delivery Note {0} created automatically").format(
				", ".join(
					frappe.utils.get_link_to_form("Delivery Note", note.name) for note in delivery_notes
				)
			),
			indicator="green",
			alert=True,
		)


def create_delivery_notes_per_sales_order(pick_list):
	"""One Delivery Note per Sales Order: erpnext groups by customer and address, merging the orders."""
	validate_item_locations(pick_list)

	sales_orders = get_picked_sales_orders(pick_list)
	delivery_notes = create_dn_with_so({name: {name} for name in sales_orders}, pick_list)

	if not all(location.sales_order for location in pick_list.locations):
		delivery_notes.append(create_dn_wo_so(pick_list))

	return delivery_notes


def get_picked_sales_orders(pick_list):
	return sorted({location.sales_order for location in pick_list.locations if location.sales_order})


def has_product_bundle_locations(pick_list):
	return any(location.product_bundle_item for location in pick_list.locations)


def has_split_product_bundle_locations(pick_list):
	"""True when a picked bundle component draws from 2+ distinct warehouses — the case the
	native mapper mishandles by collapsing the split into one over-drawn packed row."""
	warehouses_by_component = {}
	for location in pick_list.locations:
		if not location.product_bundle_item or not flt(location.picked_qty):
			continue
		key = (location.product_bundle_item, location.item_code)
		warehouses_by_component.setdefault(key, set()).add(location.warehouse)
	return any(len(warehouses) >= 2 for warehouses in warehouses_by_component.values())


def consolidate_and_create_delivery_notes(pick_list, consolidation_warehouse):
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

	# route per Sales Order by the row kinds it actually staged, not per pick list: a bundle line
	# anywhere on the pick list must not force the bundle-only builder onto a pure-pack Sales Order
	# (that builder keeps only bundle rows and would strand a standalone pack). A Sales Order carrying
	# BOTH kinds gets the mixed builder so neither line is dropped.
	for sales_order_name in get_picked_sales_orders(pick_list):
		has_bundle, has_standalone = get_sales_order_row_kinds(pick_list, sales_order_name)
		if has_bundle and has_standalone:
			create_consolidated_mixed_delivery_note(pick_list, sales_order_name, consolidation_warehouse)
		elif has_bundle:
			create_consolidated_bundle_delivery_note(pick_list, sales_order_name, consolidation_warehouse)
		else:
			create_consolidated_delivery_note(pick_list, sales_order_name, consolidation_warehouse)


def get_sales_order_row_kinds(pick_list, sales_order_name):
	"""(has_bundle, has_standalone) for one Sales Order on this pick list, by picked location kind.
	A bundle location carries product_bundle_item (the parent item); a standalone location does not."""
	has_bundle = False
	has_standalone = False
	for location in pick_list.locations:
		if location.sales_order != sales_order_name or not flt(location.picked_qty):
			continue
		if location.product_bundle_item:
			has_bundle = True
		elif location.sales_order_item:
			has_standalone = True
	return has_bundle, has_standalone


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
	# IMP: overloads delivered_qty (the field the shelf claim reads) at stage time, so
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
	reserved_qty_map = get_pick_list_reserved_qty_by_sales_order_item(pick_list.name, sales_order_name)

	kwargs = {"for_reserved_stock": True} if reserved_qty_map else None
	delivery_note = make_delivery_note(sales_order_name, kwargs=kwargs)
	reserved_batch_sre_map = get_reserved_batch_sre_map(pick_list.name, sales_order_name)
	keep_whole_pack_delivery_rows(
		delivery_note, consolidation_warehouse, staged_stock_qty, reserved_qty_map, reserved_batch_sre_map
	)

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


def get_pick_list_bundle_so_items(pick_list, sales_order_name):
	"""Sales Order Item rows of the bundles THIS pick list staged for one Sales Order."""
	return {
		location.sales_order_item
		for location in pick_list.locations
		if location.sales_order == sales_order_name
		and location.product_bundle_item
		and location.sales_order_item
		and flt(location.picked_qty)
	}


def get_staged_component_qty_map(pick_list, sales_order_name):
	"""Staged component stock (stock UOM) keyed by (bundle Sales Order Item, component item)."""
	staged_component_qty = {}
	for location in pick_list.locations:
		if location.sales_order != sales_order_name or not location.product_bundle_item:
			continue
		picked_qty = flt(location.picked_qty)
		if not picked_qty:
			continue
		key = (location.sales_order_item, location.item_code)
		staged_component_qty[key] = staged_component_qty.get(key, 0) + picked_qty
	return staged_component_qty


def create_consolidated_bundle_delivery_note(pick_list, sales_order_name, consolidation_warehouse):
	"""Split-bundle path: the components were staged into one warehouse, so build the Delivery Note
	from the Sales Order (the native pick-list→DN mapper filters on delivered_qty, which staging
	overloaded, and would map nothing) and repoint each bundle parent to the staged warehouse. On
	save make_packing_list rebuilds every packed component against that single warehouse, so the
	whole-qty packed row no longer over-draws a shelf that never held the full quantity."""
	bundle_so_items = get_pick_list_bundle_so_items(pick_list, sales_order_name)
	if not bundle_so_items:
		return

	staged_component_qty = get_staged_component_qty_map(pick_list, sales_order_name)

	delivery_note = make_delivery_note(sales_order_name)
	keep_whole_bundle_delivery_rows(
		delivery_note, consolidation_warehouse, bundle_so_items, staged_component_qty
	)

	if not delivery_note.get("items"):
		frappe.msgprint(
			_("No whole bundle staged for Sales Order {0}; stock remains in {1}").format(
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


def get_deliverable_bundle_qty(bundle_item_code, sales_order_item, staged_component_qty, ordered_bundle_qty):
	"""How many whole bundles this pick list's staged components can build, capped at the qty the
	Sales Order still expects. Partial bundles cannot ship — their components stay staged."""
	components = get_product_bundle_items(bundle_item_code)
	if not components:
		return 0

	possible_bundles = []
	for component in components:
		qty_per_bundle = flt(component.qty) or 1
		staged_qty = staged_component_qty.get((sales_order_item, component.item_code), 0)
		# flt(..., 6) before int(): raw float noise must not under-floor a whole bundle
		possible_bundles.append(int(flt(staged_qty / qty_per_bundle, 6)))

	return min(min(possible_bundles), int(flt(ordered_bundle_qty)))


def stage_whole_bundle_row(row, consolidation_warehouse, staged_component_qty):
	"""Clamp a bundle parent Delivery Note row to the whole bundles its staged components can build
	and repoint it to the consolidation warehouse. Returns True if the row ships, False if only a
	partial bundle is staged (the caller keeps its components staged for the balance)."""
	deliverable_bundles = get_deliverable_bundle_qty(
		row.item_code, row.so_detail, staged_component_qty, row.qty
	)
	if deliverable_bundles <= 0:
		return False
	row.qty = deliverable_bundles
	row.warehouse = consolidation_warehouse
	return True


def keep_whole_bundle_delivery_rows(
	delivery_note, consolidation_warehouse, bundle_so_items, staged_component_qty
):
	"""Keep only THIS pick list's bundle rows, clamp each to the whole bundles actually staged, and
	source them from the consolidation warehouse. packed_items are cleared so make_packing_list
	rebuilds them from the repointed parents on save (it always recomputes the table for a new
	mapped Delivery Note), inheriting the single staged warehouse and the clamped bundle qty."""
	kept_items = []
	remainder_bundles = []
	for row in delivery_note.get("items"):
		if row.get("so_detail") not in bundle_so_items:
			continue

		if stage_whole_bundle_row(row, consolidation_warehouse, staged_component_qty):
			kept_items.append(row)
		else:
			remainder_bundles.append(row.item_code)

	delivery_note.set("items", kept_items)
	for index, row in enumerate(delivery_note.get("items")):
		row.idx = index + 1
	delivery_note.set("packed_items", [])

	if remainder_bundles:
		frappe.msgprint(
			_("Partial bundles for {0} remain in {1} awaiting the balance components").format(
				", ".join(sorted(set(remainder_bundles))), consolidation_warehouse
			),
			indicator="orange",
			alert=True,
		)


def create_consolidated_mixed_delivery_note(pick_list, sales_order_name, consolidation_warehouse):
	"""A Sales Order carrying BOTH standalone pack lines and bundle lines maps to ONE Delivery Note
	holding both (standalone rows and bundle parents in items, bundle components in packed_items).
	Build it from the Sales Order — the native pick-list→DN mapper filters on delivered_qty, which
	staging overloaded, and would map nothing — then clamp each row by its own whole-unit floor:
	standalone rows to whole packs, bundle parents to whole bundles, both repointed to the staged
	warehouse. Only the standalone pack lines are reserved (batch-wise, so a batched pack delivers
	its exact staged batches); bundles are non-stock parents and can't reserve, so they use the
	whole-bundle floor. The DN is still built WITHOUT for_reserved_stock — that would map only the
	reserved rows and drop the bundle line — and the reserved batch bundle is re-attached per row."""
	bundle_so_items = get_pick_list_bundle_so_items(pick_list, sales_order_name)
	staged_stock_qty = get_staged_stock_qty_map(pick_list, sales_order_name)
	if not bundle_so_items and not staged_stock_qty:
		return

	staged_component_qty = get_staged_component_qty_map(pick_list, sales_order_name)

	# reserve the standalone lines only — bundle Sales Order Items are non-stock parents and reserving
	# them is a no-op that would flip their reserve_stock flag
	standalone_staged_qty = {
		so_item: qty for so_item, qty in staged_stock_qty.items() if so_item not in bundle_so_items
	}
	reserve_staged_stock(pick_list, sales_order_name, consolidation_warehouse, standalone_staged_qty)
	reserved_qty_map = get_pick_list_reserved_qty_by_sales_order_item(pick_list.name, sales_order_name)
	reserved_batch_sre_map = get_reserved_batch_sre_map(pick_list.name, sales_order_name)

	delivery_note = make_delivery_note(sales_order_name)
	keep_mixed_delivery_rows(
		delivery_note,
		consolidation_warehouse,
		bundle_so_items,
		staged_stock_qty,
		staged_component_qty,
		reserved_qty_map,
		reserved_batch_sre_map,
	)

	if not delivery_note.get("items"):
		frappe.msgprint(
			_("No whole pack or bundle staged for Sales Order {0}; stock remains in {1}").format(
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


def keep_mixed_delivery_rows(
	delivery_note,
	consolidation_warehouse,
	bundle_so_items,
	staged_stock_qty,
	staged_component_qty,
	reserved_qty_map=None,
	reserved_batch_sre_map=None,
):
	"""Keep both row kinds on one mapped Delivery Note, each clamped by its own whole-unit floor:
	bundle parent rows to whole bundles (packed components rebuilt against the staged warehouse on
	save), standalone rows to whole packs. A reserved standalone row is clamped to its reservation
	and gets its staged batch bundle re-attached so a batched pack ships its exact batches. Rows
	without staged stock, partial packs/bundles, and unreserved batch/serial rows are left staged."""
	kept_rows = []
	remainder_items = []
	remainder_bundles = []
	manual_batch_items = []
	reserved_qty_map = reserved_qty_map or {}
	remaining_reserved = dict(reserved_qty_map)
	reserved_batch_sre_map = reserved_batch_sre_map or {}
	standalone_item_codes = [
		row.item_code for row in delivery_note.get("items") if row.so_detail not in bundle_so_items
	]
	tracked_item_codes = get_tracked_item_codes(standalone_item_codes)

	for row in delivery_note.get("items"):
		if row.so_detail in bundle_so_items:
			if stage_whole_bundle_row(row, consolidation_warehouse, staged_component_qty):
				kept_rows.append(row)
			else:
				remainder_bundles.append(row.item_code)
			continue

		staged_qty = flt(staged_stock_qty.get(row.so_detail))
		if not staged_qty:
			continue

		if row.so_detail in reserved_qty_map:
			# the DN was built without for_reserved_stock (to keep the bundle rows), so this is a plain
			# SO row: clamp it to the reserved whole packs, point it at the staged warehouse, and rebuild
			# the reserved batch bundle onto it so a batched pack submits against its exact staged batches
			conversion_factor = flt(row.conversion_factor) or 1
			allowed_stock_qty = min(
				flt(row.qty) * conversion_factor, remaining_reserved.get(row.so_detail, 0)
			)
			if allowed_stock_qty <= 0:
				continue
			remaining_reserved[row.so_detail] -= allowed_stock_qty
			row.qty = allowed_stock_qty / conversion_factor
			row.warehouse = consolidation_warehouse
			attach_reserved_batch_bundle(row, reserved_batch_sre_map.get(row.so_detail))
			kept_rows.append(row)
			continue

		if row.item_code in tracked_item_codes:
			# batched with no whole-pack reservation (e.g. a partial pack): no bundle to source exact
			# batches from, so auto-picking would over-draw one batch and crash. Leave it staged.
			manual_batch_items.append(row.item_code)
			continue

		if stage_whole_pack_row(row, staged_qty, consolidation_warehouse):
			kept_rows.append(row)
		else:
			remainder_items.append(row.item_code)

	delivery_note.set("items", kept_rows)
	for index, row in enumerate(delivery_note.get("items")):
		row.idx = index + 1
	# clear so make_packing_list rebuilds bundle components against the repointed parents on save;
	# standalone rows carry none, so this only affects the kept bundle parents
	delivery_note.set("packed_items", [])

	if remainder_items:
		frappe.msgprint(
			_("Partial packs for {0} remain in {1} awaiting the balance quantity").format(
				", ".join(sorted(set(remainder_items))), consolidation_warehouse
			),
			indicator="orange",
			alert=True,
		)

	if remainder_bundles:
		frappe.msgprint(
			_("Partial bundles for {0} remain in {1} awaiting the balance components").format(
				", ".join(sorted(set(remainder_bundles))), consolidation_warehouse
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


def get_staged_batch_qty_map(pick_list, sales_order_name):
	"""Batch qty (stock UOM) staged per standalone Sales Order Item for one Sales Order:
	{sales_order_item: {batch_no: qty}}. Bundle, serial-only and plain locations contribute nothing —
	this feeds the batch-wise reservation for standalone batched packs."""
	bundle_entries_map = get_bundle_entries_map(pick_list)
	batch_qty_by_so_item = {}
	for location in pick_list.locations:
		if location.sales_order != sales_order_name or not location.sales_order_item:
			continue
		if location.product_bundle_item or not flt(location.picked_qty):
			continue
		if location.serial_and_batch_bundle:
			# ponytail: records batch entries only; a serial+batch combo item's serials are dropped,
			# giving an inconsistent multi-batch Outward bundle — split per batch if serialised-and-
			# batched items ever enter the pick flow (mirrors the transfer-side limitation)
			for entry in bundle_entries_map.get(location.serial_and_batch_bundle, []):
				if not entry.batch_no:
					continue
				batch_map = batch_qty_by_so_item.setdefault(location.sales_order_item, {})
				batch_map[entry.batch_no] = batch_map.get(entry.batch_no, 0) + abs(flt(entry.qty))
		elif location.batch_no:
			batch_map = batch_qty_by_so_item.setdefault(location.sales_order_item, {})
			batch_map[location.batch_no] = batch_map.get(location.batch_no, 0) + flt(location.picked_qty)
	return batch_qty_by_so_item


def build_reservation_batch_bundle(item_code, consolidation_warehouse, batch_qty_map):
	"""Draft Outward Serial and Batch Bundle naming the exact staged batches at the consolidation
	warehouse. Kept a draft (do_not_submit) so it posts no stock ledger entry — the reservation only
	reads its entries to become batch-wise. Returns the bundle name, or None if it built nothing."""
	if not item_code or not batch_qty_map:
		return None

	from erpnext.stock.serial_batch_bundle import SerialBatchCreation

	total_qty = sum(flt(qty) for qty in batch_qty_map.values())
	# passes "qty" not "actual_qty", so make_serial_and_batch_bundle skips the at-warehouse
	# availability check — safe because the SRE's own get_available_qty_to_reserve guard catches a
	# shortfall before the reservation commits
	bundle = SerialBatchCreation(
		{
			"item_code": item_code,
			"warehouse": consolidation_warehouse,
			# voucher_type is mandatory on the bundle; Delivery Note names its eventual consumer, but
			# no voucher_no is set — this draft is only a batch source the reservation copies entries from
			"voucher_type": "Delivery Note",
			"type_of_transaction": "Outward",
			"qty": total_qty,
			"do_not_submit": True,
		}
	).make_serial_and_batch_bundle(batch_nos=dict(batch_qty_map))
	return bundle.get("name")


def reserve_staged_stock(pick_list, sales_order_name, consolidation_warehouse, staged_stock_qty):
	"""Best-effort: reserve whole-pack multiples of the staged stock against the
	consolidation warehouse. Whole packs only: a fractional pack cannot be represented
	exactly on the Delivery Note row (reserved_qty / conversion_factor must stay whole)."""
	if not cint(frappe.db.get_single_value("Stock Settings", "enable_stock_reservation")):
		return

	sales_order = frappe.get_doc("Sales Order", sales_order_name)
	conversion_factors = {item.name: flt(item.conversion_factor) or 1 for item in sales_order.items}
	item_codes = {item.name: item.item_code for item in sales_order.items}
	staged_batch_qty = get_staged_batch_qty_map(pick_list, sales_order_name)

	items_details = []
	throwaway_bundles = []
	for sales_order_item, staged_qty in staged_stock_qty.items():
		whole_pack_stock_qty = get_whole_pack_stock_qty(
			staged_qty, conversion_factors.get(sales_order_item, 1)
		)
		if whole_pack_stock_qty <= 0:
			continue
		item_detail = {
			"sales_order_item": sales_order_item,
			"warehouse": consolidation_warehouse,
			"qty_to_reserve": whole_pack_stock_qty,
			"from_voucher_no": pick_list.name,
		}
		# a batched item needs a batch-wise reservation: a plain qty reservation leaves the reserved
		# Delivery Note row with no batch, and on submit ERPNext auto-picks a single batch and over-draws
		# it (the staged 6 = 2 of X + 4 of Y crashes as -2 on batch Y). Name the exact staged batches so
		# the reserved-stock mapper draws them precisely. The reservation clamps to qty_to_reserve, so a
		# partial pack still reserves whole packs and leaves the balance staged.
		batch_qty_map = staged_batch_qty.get(sales_order_item)
		if batch_qty_map:
			bundle_name = build_reservation_batch_bundle(
				item_codes.get(sales_order_item), consolidation_warehouse, batch_qty_map
			)
			if bundle_name:
				item_detail["serial_and_batch_bundle"] = bundle_name
				throwaway_bundles.append(bundle_name)
		items_details.append(item_detail)

	if not items_details:
		return

	try:
		create_stock_reservation_entries_for_so_items(
			sales_order, items_details, from_voucher_type="Pick List", notify=True
		)
	except Exception:
		frappe.log_error(
			title=_("Consolidation Stock Reservation Failed for Pick List {0}").format(pick_list.name)
		)
	finally:
		# the SREs copied these bundles' entries into their own child tables; the draft bundles now
		# have no referrer and would otherwise leak one dead row per batched reservation (native
		# pick-list cancel cleanup never reaches them), so drop them once reservation is done
		for bundle_name in throwaway_bundles:
			frappe.delete_doc("Serial and Batch Bundle", bundle_name, ignore_permissions=True, force=True)


def get_pick_list_reserved_qty_by_sales_order_item(pick_list_name, sales_order_name):
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


def get_reserved_batch_sre_map(pick_list_name, sales_order_name):
	"""This pick list's batch-wise reservations keyed by Sales Order Item. Only Serial-and-Batch
	reservations need a bundle rebuilt onto the Delivery Note row; qty reservations deliver as-is."""
	sre_map = {}
	for sre in frappe.get_all(
		"Stock Reservation Entry",
		filters={
			"voucher_no": sales_order_name,
			"from_voucher_no": pick_list_name,
			"docstatus": 1,
			"reservation_based_on": "Serial and Batch",
		},
		fields=["name", "voucher_detail_no", "item_code", "warehouse", "has_serial_no", "has_batch_no"],
	):
		sre_map.setdefault(sre.voucher_detail_no, []).append(sre)
	return sre_map


def attach_reserved_batch_bundle(row, sre_list):
	"""Rebuild the reserved batches onto the Delivery Note row. The reserved-stock mapper only
	attaches a Serial and Batch Bundle when use_serial_batch_fields is off; with it on (the site
	default) a multi-batch reservation lands on a bare row that reads item.serial_and_batch_bundle
	as None on submit. Build the exact Outward bundle from the reservation and point the row at it."""
	if not sre_list:
		return

	# with use_serial_batch_fields off, the reserved-stock mapper already attached a bundle; rebuilding
	# here would orphan it and duplicate the work, so only run when that mode leaves the row bare
	if not cint(frappe.db.get_single_value("Stock Settings", "use_serial_batch_fields")):
		return

	bundle_name = get_reserved_batch_bundle_name(sre_list)
	if bundle_name:
		row.serial_and_batch_bundle = bundle_name
		row.use_serial_batch_fields = 0


def whole_pack_count(staged_qty, conversion_factor):
	conversion_factor = flt(conversion_factor) or 1
	# flt(..., 6) before int(): raw float noise (5.999999999) would under-floor a whole pack
	return int(flt(flt(staged_qty) / conversion_factor, 6))


def get_whole_pack_stock_qty(staged_qty, conversion_factor):
	return whole_pack_count(staged_qty, conversion_factor) * (flt(conversion_factor) or 1)


def stage_whole_pack_row(row, staged_qty, consolidation_warehouse):
	"""Clamp a standalone Delivery Note row to the whole packs staged (never more than the row's own
	ordered qty) and repoint it to the consolidation warehouse. Returns True if the row ships, False
	if only a partial pack is staged (the caller keeps the remainder staged for the balance)."""
	whole_packs = whole_pack_count(staged_qty, flt(row.conversion_factor) or 1)
	if whole_packs <= 0:
		return False
	row.qty = min(whole_packs, flt(row.qty))
	row.warehouse = consolidation_warehouse
	return True


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


def keep_whole_pack_delivery_rows(
	delivery_note, consolidation_warehouse, staged_stock_qty, reserved_qty_map, reserved_batch_sre_map=None
):
	kept_rows = []
	remainder_items = []
	manual_batch_items = []
	remaining_reserved = dict(reserved_qty_map)
	reserved_batch_sre_map = reserved_batch_sre_map or {}
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
			# ship this reserved row, clamped to its own reservation, re-attaching its staged batch bundle.
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
			attach_reserved_batch_bundle(row, reserved_batch_sre_map.get(row.so_detail))
			kept_rows.append(row)
			continue

		if stage_whole_pack_row(row, staged_qty, consolidation_warehouse):
			kept_rows.append(row)
		else:
			remainder_items.append(row.item_code)

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
