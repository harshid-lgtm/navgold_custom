# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from erpnext.stock.doctype.item.item import get_uom_conv_factor
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, cstr, flt, orjson_dumps

from navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku import get_proxy_sku_map

# Didn't need to do this much splitting but trying to keep everything separate 😬
FBA_INBOUND_SERVICE = "navgold_custom.navgold_custom.doctype.fba_inbound.fba_inbound_service"


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
			f"{FBA_INBOUND_SERVICE}.create_plan",
			queue="long",
			job_id=f"fba_inboundcreate_plan_{self.name}",
			fba_inbound_name=self.name,
			enqueued_by=frappe.session.user,
		)

		frappe.msgprint(_("Plan creation started in the background. You will be notified when done."))

	@frappe.whitelist()
	def fetch_packing_options(self):
		if self.status != "Plan Created":
			frappe.throw(_("Packing options can only be fetched when status is 'Plan Created'"))

		frappe.enqueue(
			f"{FBA_INBOUND_SERVICE}.fetch_packing_options",
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
			f"{FBA_INBOUND_SERVICE}.confirm_packing_option",
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
			f"{FBA_INBOUND_SERVICE}.fetch_placement_options",
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
			f"{FBA_INBOUND_SERVICE}.confirm_placement_option",
			queue="long",
			job_id=f"fba_inbound_confirm_placement_{self.name}",
			fba_inbound_name=self.name,
			placement_option_id=selected[0].placement_option_id,
			shipment_ids=selected[0].shipment_ids or "",
			enqueued_by=frappe.session.user,
		)

		frappe.msgprint(_("Confirming placement option in the background. You will be notified when done."))

	@frappe.whitelist()
	def fetch_transportation_options(self, ready_to_ship_date: str = ""):
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
			f"{FBA_INBOUND_SERVICE}.fetch_transportation_options",
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
			f"{FBA_INBOUND_SERVICE}.confirm_transportation_options",
			queue="long",
			job_id=f"fba_inbound_confirm_transport_{self.name}",
			fba_inbound_name=self.name,
			transport_selections=transport_selections,
			enqueued_by=frappe.session.user,
		)

		frappe.msgprint(_("Confirming transportation in the background. You will be notified when done."))

	@frappe.whitelist()
	def get_labels(self, page_type: str = "PackageLabel_A4_2", include_pallet: bool = False):
		if self.status not in ("Transport Confirmed", "Labels Ready"):
			frappe.throw(_("Labels can only be fetched after transport is confirmed"))

		frappe.enqueue(
			f"{FBA_INBOUND_SERVICE}.get_labels",
			queue="long",
			job_id=f"fba_inboundget_labels_{self.name}",
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
				f"{FBA_INBOUND_SERVICE}.cancel_plan",
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
def get_items_from_stock_entry(stock_entry_name: str):
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
def get_boxes_from_packing_slips(stock_entry_name: str):
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
		weight_kg = flt(slip.gross_weight_pkg) * flt(get_uom_conv_factor(slip.gross_weight_uom, "Kg"))
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
				"items_detail": orjson_dumps(items_by_slip.get(slip.name, [])),
			}
		)

	return boxes
