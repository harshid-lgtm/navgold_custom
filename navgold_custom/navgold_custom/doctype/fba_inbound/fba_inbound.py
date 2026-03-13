# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, cstr, flt

from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import (
	FulfillmentInbound,
	SPAPIError,
)
from navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku import get_proxy_sku_map

OWNER_CONSTRAINT_MAP = {
	"AMAZON_ONLY": "AMAZON",
	"SELLER_ONLY": "SELLER",
	"NONE_ONLY": "NONE",
}


class FBAInbound(Document):
	def validate(self):
		if self.stock_entry:
			self.validate_stock_entry()

	def validate_stock_entry(self):
		stock_entry = frappe.db.get_value(
			"Stock Entry", self.stock_entry, ["stock_entry_type", "docstatus"], as_dict=True
		)
		if stock_entry.stock_entry_type != "FBA Transfer":
			frappe.throw(_("Stock Entry must be of type 'FBA Transfer'"))

		if cint(stock_entry.docstatus) != 1:
			frappe.throw(_("Stock Entry must be submitted before creating an FBA Inbound plan"))

	@frappe.whitelist()
	def create_plan(self):
		if self.status != "Draft":
			frappe.throw(_("Plan can only be created when status is Draft"))

		if not self.items:
			frappe.throw(_("Add items before creating the plan"))

		frappe.enqueue(
			_create_plan,
			queue="long",
			job_id=f"fba_inbound_create_plan_{self.name}",
			fba_inbound_name=self.name,
			enqueued_by=frappe.session.user,
		)

		frappe.msgprint(_("Plan creation started in the background. You will be notified when done."))

	@frappe.whitelist()
	def fetch_packing_options(self):
		if self.status != "Plan Created":
			frappe.throw(_("Packing options can only be fetched when status is 'Plan Created'"))

		frappe.enqueue(
			_fetch_packing_options,
			queue="long",
			job_id=f"fba_inbound_fetch_packing_{self.name}",
			fba_inbound_name=self.name,
			enqueued_by=frappe.session.user,
		)

		frappe.msgprint(_("Fetching packing options in the background. You will be notified when done."))

	@frappe.whitelist()
	def confirm_packing_option(self):
		if self.status != "Packing Options Ready":
			frappe.throw(_("Select and confirm a packing option first"))

		selected = [row for row in self.packing_options if row.is_selected]
		if not selected:
			frappe.throw(_("Tick 'Select' on one packing option before confirming"))
		if len(selected) > 1:
			frappe.throw(_("Only one packing option can be selected"))

		if not self.boxes:
			frappe.throw(_("No boxes found. Add box details before confirming packing."))

		frappe.enqueue(
			_confirm_packing_option,
			queue="long",
			job_id=f"fba_inbound_confirm_packing_{self.name}",
			fba_inbound_name=self.name,
			packing_option_id=selected[0].packing_option_id,
			packing_group_ids=selected[0].packing_group_ids or "",
			enqueued_by=frappe.session.user,
		)

		frappe.msgprint(
			_(
				"Confirming packing option and sending box details in the background. You will be notified when done."
			)
		)

	@frappe.whitelist()
	def fetch_placement_options(self):
		if self.status != "Packing Confirmed":
			frappe.throw(_("Placement options can only be fetched when status is 'Packing Confirmed'"))

		frappe.enqueue(
			_fetch_placement_options,
			queue="long",
			job_id=f"fba_inbound_fetch_placement_{self.name}",
			fba_inbound_name=self.name,
			enqueued_by=frappe.session.user,
		)

		frappe.msgprint(_("Fetching placement options in the background. You will be notified when done."))

	@frappe.whitelist()
	def confirm_placement_option(self):
		if self.status != "Placement Options Ready":
			frappe.throw(_("Select and confirm a placement option first"))

		selected = [row for row in self.placement_options if row.is_selected]
		if not selected:
			frappe.throw(_("Tick 'Select' on one placement option before confirming"))
		if len(selected) > 1:
			frappe.throw(_("Only one placement option can be selected"))

		frappe.enqueue(
			_confirm_placement_option,
			queue="long",
			job_id=f"fba_inbound_confirm_placement_{self.name}",
			fba_inbound_name=self.name,
			placement_option_id=selected[0].placement_option_id,
			shipment_ids=selected[0].shipment_ids or "",
			enqueued_by=frappe.session.user,
		)

		frappe.msgprint(_("Confirming placement option in the background. You will be notified when done."))

	@frappe.whitelist()
	def fetch_transportation_options(self, ready_to_ship_date=""):
		if self.status != "Placement Confirmed":
			frappe.throw(_("Transportation options require status 'Placement Confirmed'"))

		if not self.shipments:
			frappe.throw(_("No shipments found. Confirm placement first."))

		confirmed_placement = next((row for row in self.placement_options if row.is_selected), None)
		if not confirmed_placement:
			frappe.throw(_("No confirmed placement option found"))

		if not ready_to_ship_date:
			frappe.throw(_("Ready to ship date is required"))

		frappe.enqueue(
			_fetch_transportation_options,
			queue="long",
			job_id=f"fba_inbound_fetch_transport_{self.name}",
			fba_inbound_name=self.name,
			placement_option_id=confirmed_placement.placement_option_id,
			ready_to_ship_date=ready_to_ship_date,
			enqueued_by=frappe.session.user,
		)

		frappe.msgprint(
			_("Fetching transportation options in the background. You will be notified when done.")
		)

	@frappe.whitelist()
	def confirm_transportation_options(self):
		if self.status != "Transport Options Ready":
			frappe.throw(_("Confirm transport is only valid when status is 'Transport Options Ready'"))

		selected = [row for row in self.transportation_options if row.is_selected]
		if not selected:
			frappe.throw(_("Tick 'Select' on at least one transportation option before confirming"))

		seen_shipments = set()
		for row in selected:
			if row.shipment_id in seen_shipments:
				frappe.throw(
					_(
						"Only one transportation option can be selected per shipment. Conflict on shipment {0}"
					).format(row.shipment_id)
				)
			seen_shipments.add(row.shipment_id)

		transport_selections = [
			{"shipmentId": row.shipment_id, "transportationOptionId": row.transportation_option_id}
			for row in selected
		]

		frappe.enqueue(
			_confirm_transportation_options,
			queue="long",
			job_id=f"fba_inbound_confirm_transport_{self.name}",
			fba_inbound_name=self.name,
			transport_selections=transport_selections,
			enqueued_by=frappe.session.user,
		)

		frappe.msgprint(_("Confirming transportation in the background. You will be notified when done."))

	@frappe.whitelist()
	def get_labels(self, page_type="PackageLabel_A4_2", include_pallet=False):
		if self.status not in ("Transport Confirmed", "Labels Ready"):
			frappe.throw(_("Labels can only be fetched after transport is confirmed"))

		frappe.enqueue(
			_get_labels,
			queue="long",
			job_id=f"fba_inbound_get_labels_{self.name}",
			fba_inbound_name=self.name,
			page_type=page_type or "PackageLabel_A4_2",
			include_pallet=cint(include_pallet),
			enqueued_by=frappe.session.user,
		)

		frappe.msgprint(_("Fetching labels in the background. You will be notified when done."))

	@frappe.whitelist()
	def cancel_plan(self):
		if self.status == "Shipped":
			frappe.throw(_("Cannot cancel a shipped inbound plan"))

		if self.docstatus == 1:
			frappe.throw(_("Cancel the submitted document first before marking as Cancelled"))

		if self.inbound_plan_id:
			frappe.enqueue(
				_cancel_plan,
				queue="long",
				job_id=f"fba_inbound_cancel_{self.name}",
				fba_inbound_name=self.name,
				enqueued_by=frappe.session.user,
			)
			frappe.msgprint(
				_("Cancellation sent to Amazon in the background. You will be notified when done.")
			)
		else:
			frappe.db.set_value("FBA Inbound", self.name, "status", "Cancelled")


@frappe.whitelist()
def get_items_from_stock_entry(stock_entry_name):
	settings = frappe.get_cached_doc("Navgold Settings")

	stock_entry_details = frappe.get_all(
		"Stock Entry Detail",
		filters={"parent": stock_entry_name},
		fields=["item_code", "transfer_qty", "uom", "conversion_factor"],
	)

	if not stock_entry_details:
		return []

	item_codes = list({row.item_code for row in stock_entry_details})

	proxy_sku_map = {
		(data["item"], data["uom"]): {"name": name, "fnsku": data["fnsku"]}
		for name, data in get_proxy_sku_map(item_codes=item_codes, only_fba=True).items()
	}

	item_expiry_map = {
		row.name: row
		for row in frappe.get_all(
			"Item",
			filters={"name": ["in", item_codes]},
			fields=["name", "has_expiry_date", "shelf_life_in_days"],
		)
	}

	consolidated = {}
	for row in stock_entry_details:
		key = (row.item_code, row.uom)
		conversion_factor = flt(row.conversion_factor) or 1.0
		pack_qty = flt(row.transfer_qty) / conversion_factor
		if key in consolidated:
			consolidated[key]["qty"] += pack_qty
		else:
			consolidated[key] = {
				"item_code": row.item_code,
				"uom": row.uom,
				"qty": pack_qty,
			}

	default_shelf_days = cint(settings.min_shelf_expiry_in_days)
	today = frappe.utils.today()

	result = []
	for (item_code, uom), data in consolidated.items():
		proxy_sku = proxy_sku_map.get((item_code, uom))
		if not proxy_sku:
			frappe.msgprint(
				_("No active FBA Proxy SKU found for item {0} / UOM {1}. Row skipped.").format(
					item_code, uom
				),
				indicator="orange",
				alert=True,
			)
			continue

		expiration_date = None
		item_info = item_expiry_map.get(item_code)
		if item_info and item_info.has_expiry_date:
			shelf_days = cint(item_info.shelf_life_in_days) or default_shelf_days
			if shelf_days:
				expiration_date = add_days(today, shelf_days)

		result.append(
			{
				"item_code": item_code,
				"uom": uom,
				"msku": proxy_sku["name"],
				"fnsku": proxy_sku["fnsku"],
				"quantity": cint(round(data["qty"])),
				"prep_owner": settings.default_prep_owner or "SELLER",
				"label_owner": settings.default_label_owner or "SELLER",
				"expiration_date": expiration_date,
			}
		)

	return result


@frappe.whitelist()
def get_boxes_from_packing_slips(stock_entry_name):
	packing_slips = frappe.get_all(
		"FBA Packing Slip",
		filters={"stock_entry": stock_entry_name, "docstatus": 1},
		fields=["name", "gross_weight_pkg", "gross_weight_uom", "depth_cm", "height_cm", "width_cm"],
	)

	if not packing_slips:
		return []

	packing_slip_names = [slip.name for slip in packing_slips]

	packing_slip_items = frappe.get_all(
		"Packing Slip Item",
		filters={"parent": ["in", packing_slip_names], "parenttype": "FBA Packing Slip"},
		fields=["parent", "item_code", "item_name", "qty"],
	)

	items_by_slip = {}
	for item in packing_slip_items:
		items_by_slip.setdefault(item.parent, []).append(
			{"item_code": item.item_code, "item_name": item.item_name, "qty": flt(item.qty)}
		)

	boxes = []
	for slip in packing_slips:
		weight_kg = convert_weight_to_kg(flt(slip.gross_weight_pkg), slip.gross_weight_uom or "KG")
		boxes.append(
			{
				"fba_packing_slip": slip.name,
				"weight": weight_kg,
				"weight_unit": "KG",
				"length": flt(slip.depth_cm),
				"width": flt(slip.width_cm),
				"height": flt(slip.height_cm),
				"dimension_unit": "CM",
				"quantity": 1,
				"items_detail": frappe.as_json(items_by_slip.get(slip.name, [])),
			}
		)

	return boxes


def _create_plan(fba_inbound_name, enqueued_by="Administrator"):
	doc = frappe.get_cached_doc("FBA Inbound", fba_inbound_name)
	settings = frappe.get_cached_doc("Navgold Settings")
	source_address = build_source_address(settings)
	inbound_api = FulfillmentInbound.from_amazon_settings()
	prep_details_map = get_prep_details_map(inbound_api, doc.items)

	items_payload = []
	for item in doc.items:
		prep_owner = item.prep_owner or "SELLER"
		label_owner = item.label_owner or "SELLER"

		prep_detail = prep_details_map.get(item.msku)
		if prep_detail:
			prep_owner = resolve_owner_constraint(prep_detail.get("prepOwnerConstraint"), prep_owner)
			label_owner = resolve_owner_constraint(prep_detail.get("labelOwnerConstraint"), label_owner)

		item.prep_owner = prep_owner
		item.label_owner = label_owner

		item_entry = {
			"msku": item.msku,
			"quantity": cint(item.quantity),
			"prepOwner": prep_owner,
			"labelOwner": label_owner,
		}
		if item.expiration_date:
			item_entry["expiration"] = cstr(item.expiration_date)
		items_payload.append(item_entry)

	try:
		response = inbound_api.create_inbound_plan(
			name=fba_inbound_name,
			source_address=source_address,
			destination_marketplaces=[inbound_api.marketplace_id],
			items=items_payload,
		)

		operation_id = response.get("operationId")
		inbound_plan_id = response.get("inboundPlanId")

		if operation_id:
			inbound_api.poll_operation(operation_id)

		if not inbound_plan_id and operation_id:
			operation_result = inbound_api.get_operation(operation_id)
			inbound_plan_id = (
				operation_result.get("operationResult", {}).get("inboundPlanId") or inbound_plan_id
			)

		doc.inbound_plan_id = inbound_plan_id
		doc.operation_id = operation_id
		doc.status = "Plan Created"
		doc.save(ignore_permissions=True)

		frappe.publish_realtime(
			"fba_inbound_plan_created",
			{"name": fba_inbound_name, "inbound_plan_id": inbound_plan_id},
			user=enqueued_by,
		)

	except SPAPIError as error:
		handle_step_error("Plan Creation", fba_inbound_name, error, enqueued_by, "create_plan")


def _fetch_packing_options(fba_inbound_name, enqueued_by="Administrator"):
	doc = frappe.get_cached_doc("FBA Inbound", fba_inbound_name)
	inbound_api = FulfillmentInbound.from_amazon_settings()

	try:
		generate_response = inbound_api.generate_packing_options(doc.inbound_plan_id)
		generate_operation_id = generate_response.get("operationId")
		if generate_operation_id:
			inbound_api.poll_operation(generate_operation_id)

		list_response = inbound_api.list_packing_options(doc.inbound_plan_id)
		packing_options = list_response.get("packingOptions", [])

		doc.packing_options = []
		for option in packing_options:
			fees_total = sum(flt(fee.get("value", {}).get("amount", 0)) for fee in option.get("fees", []))
			discounts_total = sum(
				flt(discount.get("value", {}).get("amount", 0)) for discount in option.get("discounts", [])
			)
			shipping_modes = ", ".join(
				f"{shipping_config.get('shippingSolution', '')}/{shipping_config.get('shippingMode', '')}"
				for shipping_config in option.get("supportedShippingConfigurations", [])
			)
			packing_groups = ", ".join(option.get("packingGroups", []))
			doc.append(
				"packing_options",
				{
					"packing_option_id": option.get("packingOptionId"),
					"status": option.get("status"),
					"fees": fees_total,
					"discounts": discounts_total,
					"supported_shipping_modes": shipping_modes,
					"packing_group_ids": packing_groups,
				},
			)

		doc.status = "Packing Options Ready"
		doc.save(ignore_permissions=True)

		frappe.publish_realtime(
			"fba_inbound_packing_options_ready",
			{"name": fba_inbound_name, "count": len(packing_options)},
			user=enqueued_by,
		)

	except SPAPIError as error:
		handle_step_error(
			"Fetch Packing Options", fba_inbound_name, error, enqueued_by, "fetch_packing_options"
		)


def _confirm_packing_option(
	fba_inbound_name,
	packing_option_id,
	packing_group_ids="",
	enqueued_by="Administrator",
):
	doc = frappe.get_cached_doc("FBA Inbound", fba_inbound_name)
	inbound_api = FulfillmentInbound.from_amazon_settings()

	try:
		confirm_response = inbound_api.confirm_packing_option(doc.inbound_plan_id, packing_option_id)
		confirm_operation_id = confirm_response.get("operationId")
		if confirm_operation_id:
			inbound_api.poll_operation(confirm_operation_id)

		packing_group_id = (packing_group_ids or "").split(",")[0].strip() or None
		boxes_payload = build_boxes_payload(doc, packing_group_id)

		packing_info_response = inbound_api.set_packing_information(
			doc.inbound_plan_id,
			{"packageGroupings": boxes_payload},
		)
		packing_info_operation_id = packing_info_response.get("operationId")
		if packing_info_operation_id:
			inbound_api.poll_operation(packing_info_operation_id)

		frappe.db.set_value("FBA Inbound", fba_inbound_name, "status", "Packing Confirmed")

		frappe.publish_realtime(
			"fba_inbound_packing_confirmed",
			{"name": fba_inbound_name},
			user=enqueued_by,
		)

	except SPAPIError as error:
		handle_step_error("Confirm Packing", fba_inbound_name, error, enqueued_by, "confirm_packing")


def _fetch_placement_options(fba_inbound_name, enqueued_by="Administrator"):
	doc = frappe.get_cached_doc("FBA Inbound", fba_inbound_name)
	inbound_api = FulfillmentInbound.from_amazon_settings()

	try:
		generate_response = inbound_api.generate_placement_options(doc.inbound_plan_id)
		generate_operation_id = generate_response.get("operationId")
		if generate_operation_id:
			inbound_api.poll_operation(generate_operation_id)

		list_response = inbound_api.list_placement_options(doc.inbound_plan_id)
		placement_options = list_response.get("placementOptions", [])

		doc.placement_options = []
		for option in placement_options:
			fees_total = sum(flt(fee.get("value", {}).get("amount", 0)) for fee in option.get("fees", []))
			discounts_total = sum(
				flt(discount.get("value", {}).get("amount", 0)) for discount in option.get("discounts", [])
			)
			shipment_ids = ", ".join(option.get("shipmentIds", []))
			doc.append(
				"placement_options",
				{
					"placement_option_id": option.get("placementOptionId"),
					"status": option.get("status"),
					"fees": fees_total,
					"discounts": discounts_total,
					"shipment_ids": shipment_ids,
				},
			)

		doc.status = "Placement Options Ready"
		doc.save(ignore_permissions=True)

		frappe.publish_realtime(
			"fba_inbound_placement_options_ready",
			{"name": fba_inbound_name, "count": len(placement_options)},
			user=enqueued_by,
		)

	except SPAPIError as error:
		handle_step_error(
			"Fetch Placement Options", fba_inbound_name, error, enqueued_by, "fetch_placement_options"
		)


def _confirm_placement_option(
	fba_inbound_name,
	placement_option_id,
	shipment_ids="",
	enqueued_by="Administrator",
):
	doc = frappe.get_cached_doc("FBA Inbound", fba_inbound_name)
	inbound_api = FulfillmentInbound.from_amazon_settings()

	try:
		confirm_response = inbound_api.confirm_placement_option(doc.inbound_plan_id, placement_option_id)
		confirm_operation_id = confirm_response.get("operationId")
		if confirm_operation_id:
			inbound_api.poll_operation(confirm_operation_id)

		shipment_id_list = [s.strip() for s in (shipment_ids or "").split(",") if s.strip()]
		doc.shipments = []

		for shipment_id in shipment_id_list:
			shipment_data = inbound_api.get_shipment(doc.inbound_plan_id, shipment_id)
			destination = shipment_data.get("destination", {})
			dest_address = destination.get("address", {})
			dest_address_str = ", ".join(
				filter(
					None,
					[
						dest_address.get("addressLine1"),
						dest_address.get("city"),
						dest_address.get("countryCode"),
					],
				)
			)
			doc.append(
				"shipments",
				{
					"shipment_id": shipment_id,
					"shipment_confirmation_id": shipment_data.get("shipmentConfirmationId", ""),
					"status": shipment_data.get("status", ""),
					"destination_warehouse_id": destination.get("warehouseId", ""),
					"destination_address": dest_address_str,
				},
			)

		doc.status = "Placement Confirmed"
		doc.save(ignore_permissions=True)

		frappe.publish_realtime(
			"fba_inbound_placement_confirmed",
			{"name": fba_inbound_name, "shipment_count": len(shipment_id_list)},
			user=enqueued_by,
		)

	except SPAPIError as error:
		handle_step_error("Confirm Placement", fba_inbound_name, error, enqueued_by, "confirm_placement")


def _fetch_transportation_options(
	fba_inbound_name,
	placement_option_id="",
	ready_to_ship_date="",
	enqueued_by="Administrator",
):
	doc = frappe.get_cached_doc("FBA Inbound", fba_inbound_name)
	inbound_api = FulfillmentInbound.from_amazon_settings()

	try:
		shipment_ids = [row.shipment_id for row in doc.shipments if row.shipment_id]

		ready_start = f"{ready_to_ship_date}T00:00:00Z"
		ready_end = f"{add_days(ready_to_ship_date, 1)}T00:00:00Z"

		shipment_configs = [
			{
				"shipmentId": shipment_id,
				"readyToShipWindow": {"start": ready_start, "end": ready_end},
			}
			for shipment_id in shipment_ids
		]

		generate_response = inbound_api.generate_transportation_options(
			doc.inbound_plan_id,
			{
				"placementOptionId": placement_option_id,
				"shipmentTransportationConfigurations": shipment_configs,
			},
		)
		generate_operation_id = generate_response.get("operationId")
		if generate_operation_id:
			inbound_api.poll_operation(generate_operation_id)

		list_response = inbound_api.list_transportation_options(
			doc.inbound_plan_id, placement_option_id=placement_option_id
		)
		transport_options = list_response.get("transportationOptions", [])

		doc.transportation_options = []
		for option in transport_options:
			quote = option.get("quote", {})
			cost = quote.get("cost", {})
			carrier = option.get("carrier", {})
			doc.append(
				"transportation_options",
				{
					"transportation_option_id": option.get("transportationOptionId"),
					"shipment_id": option.get("shipmentId"),
					"shipping_solution": option.get("shippingSolution"),
					"shipping_mode": option.get("shippingMode"),
					"carrier_name": carrier.get("name", ""),
					"cost": flt(cost.get("amount", 0)),
					"cost_currency": cost.get("code", ""),
				},
			)

		doc.status = "Transport Options Ready"
		doc.save(ignore_permissions=True)

		frappe.publish_realtime(
			"fba_inbound_transport_options_ready",
			{"name": fba_inbound_name, "count": len(transport_options)},
			user=enqueued_by,
		)

	except SPAPIError as error:
		handle_step_error(
			"Fetch Transport Options", fba_inbound_name, error, enqueued_by, "fetch_transport_options"
		)


def _confirm_transportation_options(
	fba_inbound_name,
	transport_selections,
	enqueued_by="Administrator",
):
	doc = frappe.get_cached_doc("FBA Inbound", fba_inbound_name)
	inbound_api = FulfillmentInbound.from_amazon_settings()

	try:
		response = inbound_api.confirm_transportation_options(
			doc.inbound_plan_id,
			{"transportationSelections": transport_selections},
		)
		operation_id = response.get("operationId")
		if operation_id:
			inbound_api.poll_operation(operation_id)

		selection_map = {sel["shipmentId"]: sel["transportationOptionId"] for sel in transport_selections}
		for shipment_row in doc.shipments:
			if shipment_row.shipment_id in selection_map:
				shipment_row.selected_transportation_option_id = selection_map[shipment_row.shipment_id]

		doc.status = "Transport Confirmed"
		doc.save(ignore_permissions=True)

		frappe.publish_realtime(
			"fba_inbound_transport_confirmed",
			{"name": fba_inbound_name},
			user=enqueued_by,
		)

	except SPAPIError as error:
		handle_step_error("Confirm Transport", fba_inbound_name, error, enqueued_by, "confirm_transport")


def _get_labels(
	fba_inbound_name,
	page_type="PackageLabel_A4_2",
	include_pallet=0,
	enqueued_by="Administrator",
):
	doc = frappe.get_cached_doc("FBA Inbound", fba_inbound_name)
	inbound_api = FulfillmentInbound.from_amazon_settings()
	try:
		label_urls = []

		for shipment_row in doc.shipments:
			if not shipment_row.shipment_id:
				continue

			shipment_confirmation_id = shipment_row.shipment_confirmation_id
			if not shipment_confirmation_id:
				frappe.log_error(
					title=f"FBA Label Skipped: no confirmation ID for {shipment_row.shipment_id}",
					message="shipment_confirmation_id is empty",
				)
				continue

			number_of_packages = sum(cint(box.quantity) or 1 for box in doc.boxes)

			try:
				label_response = inbound_api.get_labels_v0(
					shipment_confirmation_id=shipment_confirmation_id,
					page_type=page_type,
					page_size=number_of_packages,
				)
			except Exception as label_error:
				frappe.log_error(
					title=f"FBA Box Label Fetch Failed: {shipment_confirmation_id}",
					message=str(label_error),
				)
				continue

			download_url = label_response.get("DownloadURL") or label_response.get("downloadUrl")
			if download_url:
				pdf_content = inbound_api.download_label_pdf(download_url)
				file_url = save_label_file(
					f"BoxLabels-{shipment_confirmation_id}.pdf",
					pdf_content,
					fba_inbound_name,
				)
				label_urls.append(file_url)

			if include_pallet:
				try:
					pallet_response = inbound_api.get_labels_v0(
						shipment_confirmation_id=shipment_confirmation_id,
						page_type="PackageLabel_Plain_Paper_Pallet",
						page_size=1,
					)
					pallet_url = pallet_response.get("DownloadURL") or pallet_response.get("downloadUrl")
					if pallet_url:
						pallet_content = inbound_api.download_label_pdf(pallet_url)
						file_url = save_label_file(
							f"PalletLabels-{shipment_confirmation_id}.pdf",
							pallet_content,
							fba_inbound_name,
						)
						label_urls.append(file_url)
				except Exception as pallet_error:
					frappe.log_error(
						title=f"FBA Pallet Label Fetch Skipped: {shipment_confirmation_id}",
						message=str(pallet_error),
					)

		if label_urls:
			doc.box_labels = label_urls[0]
		doc.status = "Labels Ready"
		doc.save(ignore_permissions=True)

		frappe.publish_realtime(
			"fba_inbound_labels_ready",
			{"name": fba_inbound_name, "label_urls": label_urls},
			user=enqueued_by,
		)

	except Exception as error:
		error_detail = str(error)
		frappe.log_error(
			title=f"FBA Inbound Get Labels Failed: {fba_inbound_name}",
			message=error_detail,
		)
		frappe.publish_realtime(
			"fba_inbound_step_failed",
			{"name": fba_inbound_name, "step": "get_labels", "error": error_detail},
			user=enqueued_by,
		)


def _cancel_plan(fba_inbound_name, enqueued_by="Administrator"):
	doc = frappe.get_cached_doc("FBA Inbound", fba_inbound_name)
	inbound_api = FulfillmentInbound.from_amazon_settings()

	try:
		response = inbound_api.cancel_inbound_plan(doc.inbound_plan_id)
		operation_id = response.get("operationId")
		if operation_id:
			inbound_api.poll_operation(operation_id)

		frappe.db.set_value("FBA Inbound", fba_inbound_name, "status", "Cancelled")

		frappe.publish_realtime(
			"fba_inbound_cancelled",
			{"name": fba_inbound_name},
			user=enqueued_by,
		)

	except SPAPIError as error:
		handle_step_error("Cancel Plan", fba_inbound_name, error, enqueued_by, "cancel_plan")


def build_source_address(settings):
	if not settings.fba_source_address:
		frappe.throw(_("Source address is not configured in Navgold Settings (FBA Inbound tab)"))

	address_doc = frappe.get_cached_doc("Address", settings.fba_source_address)

	if not address_doc.address_line1:
		frappe.throw(_("Address Line 1 is missing on the selected source address"))

	country_code = frappe.db.get_value("Country", address_doc.country, "code") or "GB"

	payload = {
		"name": address_doc.address_title or "",
		"addressLine1": address_doc.address_line1,
		"city": address_doc.city or "",
		"countryCode": country_code.upper(),
		"postalCode": address_doc.pincode or "",
		"phoneNumber": address_doc.phone or "",
	}

	if address_doc.address_line2:
		payload["addressLine2"] = address_doc.address_line2
	if address_doc.state:
		payload["stateOrProvinceCode"] = address_doc.state

	return payload


def build_boxes_payload(doc, packing_group_id=None):
	items_by_item_code = {item.item_code: item for item in doc.items}

	boxes = []
	for box in doc.boxes:
		items_in_box = []
		if box.items_detail:
			raw_items = frappe.parse_json(box.items_detail)
			for raw_item in raw_items:
				inbound_item = items_by_item_code.get(raw_item.get("item_code"))
				if not inbound_item:
					continue

				pack_qty = cint(round(flt(raw_item.get("qty", 0))))
				item_entry = {
					"msku": inbound_item.msku,
					"quantity": pack_qty,
					"prepOwner": inbound_item.prep_owner or "SELLER",
					"labelOwner": inbound_item.label_owner or "SELLER",
				}
				if inbound_item.expiration_date:
					item_entry["expiration"] = cstr(inbound_item.expiration_date)
				items_in_box.append(item_entry)

		boxes.append(
			{
				"weight": {"unit": "KG", "value": flt(box.weight)},
				"dimensions": {
					"unitOfMeasurement": "CM",
					"length": flt(box.length),
					"width": flt(box.width),
					"height": flt(box.height),
				},
				"quantity": cint(box.quantity) or 1,
				"contentInformationSource": "BOX_CONTENT_PROVIDED",
				"items": items_in_box,
			}
		)

	grouping = {"boxes": boxes}
	if packing_group_id:
		grouping["packingGroupId"] = packing_group_id

	return [grouping]


def convert_weight_to_kg(weight, uom):
	uom_lower = (uom or "").lower()
	if uom_lower in ("gram", "g", "grams"):
		return round(weight / 1000, 4)
	if uom_lower in ("lb", "lbs", "pound", "pounds"):
		return round(weight * 0.453592, 4)
	return weight


def save_label_file(file_name, content, fba_inbound_name):
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": "FBA Inbound",
			"attached_to_name": fba_inbound_name,
			"content": content,
			"is_private": 0,
		}
	)
	file_doc.save(ignore_permissions=True)
	return file_doc.file_url


def get_prep_details_map(inbound_api, items):
	msku_list = list({item.msku for item in items if item.msku})
	if not msku_list:
		return {}

	try:
		response = inbound_api.list_prep_details(msku_list, inbound_api.marketplace_id)
		return {
			detail["msku"]: detail for detail in response.get("mskuPrepDetails", []) if detail.get("msku")
		}
	except SPAPIError as error:
		frappe.log_error(
			title="FBA Prep Details Fetch Failed",
			message=f"{error.error}: {error.error_description}",
		)
		return {}


def resolve_owner_constraint(constraint, current_value):
	return OWNER_CONSTRAINT_MAP.get(constraint, current_value)


def handle_step_error(step_label, fba_inbound_name, error, enqueued_by, step_key):
	error_detail = f"{error.error}: {error.error_description}"
	frappe.log_error(
		title=f"FBA Inbound {step_label} Failed: {fba_inbound_name}",
		message=error_detail,
	)
	frappe.publish_realtime(
		"fba_inbound_step_failed",
		{"name": fba_inbound_name, "step": step_key, "error": error_detail},
		user=enqueued_by,
	)
