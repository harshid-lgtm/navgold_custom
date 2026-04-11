# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import frappe
from frappe import _

from navgold_custom.navgold_custom.utils import get_free_qty_map


def select_strategy(pricing_doc) -> str | None:
	"""
	Select the active strategy for an Amazon Product Pricing record.

	Strategy selection priority:
	1. FBA Strategy - If FBA stock > 0
	2. SFP Strategy - If Seller Fulfilled Prime is available
	3. Backorder Strategy - If product has a restock date
	4. Default Strategy - Fallback

	Args:
		pricing_doc: Amazon Product Pricing document

	Returns:
		Strategy name or None if no strategy available
	"""
	# Get stock information for this product
	stock_info = get_stock_info(pricing_doc.proxy_sku)

	# Priority 1: FBA Strategy
	if pricing_doc.fba_strategy and stock_info.get("fba_stock", 0) > 0:
		strategy = frappe.get_cached_doc("Amazon Product Strategy", pricing_doc.fba_strategy)
		if strategy.is_active:
			return pricing_doc.fba_strategy

	# Priority 2: SFP Strategy (Seller Fulfilled Prime - when no restock date)
	if pricing_doc.sfp_strategy and not stock_info.get("has_restock_date", False):
		strategy = frappe.get_cached_doc("Amazon Product Strategy", pricing_doc.sfp_strategy)
		if strategy.is_active:
			return pricing_doc.sfp_strategy

	# Priority 3: Backorder Strategy (when has restock date)
	if pricing_doc.backorder_strategy and stock_info.get("has_restock_date", False):
		strategy = frappe.get_cached_doc("Amazon Product Strategy", pricing_doc.backorder_strategy)
		if strategy.is_active:
			return pricing_doc.backorder_strategy

	# Priority 4: Default Strategy (fallback)
	if pricing_doc.default_strategy:
		strategy = frappe.get_cached_doc("Amazon Product Strategy", pricing_doc.default_strategy)
		if strategy.is_active:
			return pricing_doc.default_strategy

	return None


def get_stock_info(proxy_sku: str) -> dict:
	"""
	Get stock information for a Proxy SKU.

	This function checks:
	- FBA warehouse stock (AFN warehouse)
	- MFN warehouse stock
	- Restock date information

	Args:
		proxy_sku: The Proxy SKU name

	Returns:
		Dictionary with stock information:
		- fba_stock: FBA warehouse quantity
		- mfn_stock: MFN warehouse quantity
		- has_restock_date: Whether product has expected restock
	"""

	proxy = frappe.get_cached_doc("Proxy SKU", proxy_sku)

	sp_api_settings = frappe.get_single("Amazon SP API Settings")

	fba_stock = 0
	mfn_stock = 0

	# Calculate stock from Bin table
	if sp_api_settings.afn_warehouse:
		afn_qty = (
			frappe.db.get_value(
				"Bin", {"item_code": proxy.item, "warehouse": sp_api_settings.afn_warehouse}, "actual_qty"
			)
			or 0
		)
		fba_stock += afn_qty

	# MFN stock (All warehouse)
	mfn_qty = get_free_qty_map([proxy.item]).get(proxy.item, {}).get("free_qty", 0)
	mfn_stock += mfn_qty

	# Check for restock
	restock_info = check_pending_restock(proxy.item)

	return {
		"fba_stock": fba_stock,
		"mfn_stock": mfn_stock,
		"has_restock_date": restock_info["has_restock"],
		"restock_date": restock_info["restock_date"],
		"total_stock": fba_stock + mfn_stock,
	}


def check_pending_restock(item: str) -> dict:
	"""
	Check if there's a pending restock for this item.

	Checks:
	- Open Material Requests with expected delivery date
	- Open Purchase Orders with expected delivery date

	Returns:
		Dictionary with:
		- has_restock: bool
		- restock_date: date or None (latest expected delivery date from PO)
	"""

	mr_exists = frappe.db.exists(
		"Material Request Item",
		{
			"item_code": item,
			"docstatus": 1,
			"parent": [
				"in",
				frappe.get_all(
					"Material Request",
					filters={"docstatus": 1, "status": ["not in", ["Stopped", "Cancelled"]]},
					pluck="name",
				),
			],
		},
	)

	open_po_names = frappe.get_all(
		"Purchase Order",
		filters={"docstatus": 1, "status": ["not in", ["Closed", "Cancelled", "Completed"]]},
		pluck="name",
	)

	restock_date = None
	if open_po_names:
		po_items = frappe.get_all(
			"Purchase Order Item",
			filters={"item_code": item, "docstatus": 1, "parent": ["in", open_po_names]},
			fields=["schedule_date"],
			order_by="schedule_date desc",
			limit=1,
		)
		if po_items:
			restock_date = po_items[0].schedule_date

	has_restock = bool(mr_exists) or bool(restock_date)

	return {"has_restock": has_restock, "restock_date": restock_date}


def refresh_active_strategy(pricing_name: str):
	"""
	Refresh the active strategy for a pricing record.

	Args:
		pricing_name: Name of the Amazon Product Pricing record
	"""
	pricing = frappe.get_doc("Amazon Product Pricing", pricing_name)
	old_strategy = pricing.active_strategy

	pricing.select_active_strategy()
	pricing.update_current_prices()

	if pricing.active_strategy != old_strategy:
		pricing.save()
		frappe.msgprint(
			_("Active strategy changed from {0} to {1}").format(
				old_strategy or "None", pricing.active_strategy or "None"
			)
		)

	return pricing.active_strategy


@frappe.whitelist()
def bulk_refresh_strategies():
	pricing_records = frappe.get_all("Amazon Product Pricing", filters={"is_active": 1}, pluck="name")

	updated = 0
	for pricing_name in pricing_records:
		try:
			pricing = frappe.get_cached_doc("Amazon Product Pricing", pricing_name)
			old_strategy = pricing.active_strategy

			pricing.select_active_strategy()
			pricing.update_current_prices()

			if pricing.active_strategy != old_strategy:
				pricing.save()
				updated += 1
		except Exception as e:
			frappe.log_error(f"Strategy refresh error for {pricing_name}: {e!s}", "Strategy Refresh")

	return {"total": len(pricing_records), "updated": updated}
