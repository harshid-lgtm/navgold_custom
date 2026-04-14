# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import cint, cstr, flt

from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import SPAPIError

OWNER_CONSTRAINT_MAP = {
	"AMAZON_ONLY": "AMAZON",
	"SELLER_ONLY": "SELLER",
	"NONE_ONLY": "NONE",
}


def resolve_owner_constraint(constraint, current_value):
	return OWNER_CONSTRAINT_MAP.get(constraint, current_value)


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
