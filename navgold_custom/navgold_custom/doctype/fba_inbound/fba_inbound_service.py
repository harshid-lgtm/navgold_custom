# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, cint, cstr, flt

from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import (
	FulfillmentInbound,
	SPAPIError,
)
from navgold_custom.navgold_custom.doctype.fba_inbound.fba_inbound_utils import (
	build_boxes_payload,
	get_prep_details_map,
	resolve_owner_constraint,
)


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


def create_plan(fba_inbound_name, enqueued_by="Administrator"):
	doc = frappe.get_cached_doc("FBA Inbound", fba_inbound_name)
	settings = frappe.get_cached_doc("Navgold Settings")
	source_address = settings.get_source_address(state_key="stateOrProvinceCode")
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


def fetch_packing_options(fba_inbound_name, enqueued_by="Administrator"):
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


def confirm_packing_option(
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


def fetch_placement_options(fba_inbound_name, enqueued_by="Administrator"):
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


def confirm_placement_option(
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
			dest_address_parts = [
				value
				for value in (
					dest_address.get("addressLine1"),
					dest_address.get("city"),
					dest_address.get("countryCode"),
				)
				if value
			]
			dest_address_str = ", ".join(dest_address_parts)
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


def fetch_transportation_options(
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


def confirm_transportation_options(
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


def get_labels(
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
				file_doc = frappe.get_doc(
					{
						"doctype": "File",
						"file_name": f"BoxLabels-{shipment_confirmation_id}.pdf",
						"attached_to_doctype": "FBA Inbound",
						"attached_to_name": fba_inbound_name,
						"content": pdf_content,
						"is_private": 0,
					}
				)
				file_doc.insert(ignore_permissions=True).save()
				label_urls.append(file_doc.file_url)

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
						pallet_file_doc = frappe.get_doc(
							{
								"doctype": "File",
								"file_name": f"PalletLabels-{shipment_confirmation_id}.pdf",
								"attached_to_doctype": "FBA Inbound",
								"attached_to_name": fba_inbound_name,
								"content": pallet_content,
								"is_private": 0,
							}
						)
						pallet_file_doc.insert(ignore_permissions=True).save()
						label_urls.append(pallet_file_doc.file_url)
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


def cancel_plan(fba_inbound_name, enqueued_by="Administrator"):
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
