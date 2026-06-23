import frappe
from frappe.utils.data import flt, now_datetime

from navgold_custom.navgold_custom.amazon_pricing.report_attachments import save_last_report_attachment
from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import FBAInventory
from navgold_custom.navgold_custom.utils import ITEM_AFN_FIELD, get_conversion_factor


@frappe.whitelist()
def bulk_fetch_afn_stock():
	frappe.only_for("System Manager")

	# Share job_id/deduplicate with the scheduled run so a manual click during a scheduled
	# run does not double-pull from Amazon.
	frappe.enqueue(
		"navgold_custom.navgold_custom.amazon_pricing.afn_stock_fetcher.process_afn_stock_fetch",
		job_id="navgold.scheduled_bulk_fetch_afn_stock",
		deduplicate=True,
		queue="long",
		timeout=1800,
	)


def process_afn_stock_fetch():
	proxy_rows = frappe.get_all(
		"Proxy SKU",
		filters={"is_fba": 1, "is_active": 1},
		fields=["name", "item", "uom", "afn_stock"],
	)

	if not proxy_rows:
		frappe.publish_realtime(
			"msgprint",
			"No active FBA Proxy SKUs found. Nothing to fetch.",
			user=frappe.session.user,
		)
		return

	seller_skus = [proxy_row["name"] for proxy_row in proxy_rows]

	# A failed fetch must not zero existing stock; only a successful API call drives the
	# zero-missing behavior below. Keep the network call isolated in its own try/except.
	try:
		inventory = FBAInventory.from_amazon_settings().fetch_for_skus(seller_skus)
	except Exception as e:
		frappe.log_error(f"AFN stock FBA Inventory fetch failed: {e!s}", "AFN Stock Fetch")
		frappe.publish_realtime(
			"msgprint",
			"AFN stock could not be fetched from Amazon. Existing stock values were not changed.",
			user=frappe.session.user,
		)
		from navgold_custom.navgold_custom.amazon_pricing.alerts import notify

		notify("FBA stock pull FAILED", [f"Error: {str(e)[:300]}"], dedupe_key="fba_pull_error")
		return

	# FBA stock comes from the Inventory API, not a downloadable report
	save_last_report_attachment("last_afn_stock_report", "amazon_afn_stock.json", frappe.as_json(inventory))

	# Raw per-SKU fulfillable qty in the proxy SKU's own UOM. SKUs Amazon did not return are
	# treated as 0 (sold out / no FBA listing) — this drives zero-missing.
	sku_qty_map = {
		proxy_row["name"]: flt(inventory.get(proxy_row["name"], {}).get("fulfillableQuantity", 0), 4)
		for proxy_row in proxy_rows
	}

	proxy_updates = compute_afn_stock_updates(proxy_rows, sku_qty_map, field="afn_stock")
	if proxy_updates:
		frappe.db.bulk_update("Proxy SKU", proxy_updates, update_modified=False)

	# Aggregate per Item into base UOM. Amazon sells the proxy UOM (e.g. a 6-pack), so multiply
	# by the conversion factor to land in stock UOM units. get_conversion_factor is redis-cached.
	item_totals = compute_item_afn_totals(proxy_rows, sku_qty_map, get_conversion_factor)

	# Items that previously held stock but no longer have any contributing active FBA proxy row
	# (e.g. their last FBA Proxy SKU was deactivated) must be zeroed too.
	items_with_stock = frappe.get_all(
		"Item",
		filters={ITEM_AFN_FIELD: [">", 0]},
		pluck="name",
	)
	for item in items_with_stock:
		item_totals.setdefault(item, 0.0)

	current_item_rows = (
		frappe.get_all(
			"Item",
			filters={"name": ["in", list(item_totals.keys())]},
			fields=["name", ITEM_AFN_FIELD],
		)
		if item_totals
		else []
	)

	item_updates = compute_afn_stock_updates(current_item_rows, item_totals, field=ITEM_AFN_FIELD)
	if item_updates:
		frappe.db.bulk_update("Item", item_updates, update_modified=False)

	frappe.db.set_single_value(
		"Navgold Settings",
		{
			"last_afn_stock_synced_at": now_datetime(),
			"last_afn_stock_proxy_count": len(proxy_updates),
			"last_afn_stock_items_count": len(item_updates),
		},
	)

	frappe.db.commit()  # nosemgrep: frappe-manual-commit -- background job, persist before notifying user

	frappe.publish_realtime(
		"msgprint",
		f"AFN Stock Fetch Complete: Proxy SKUs updated {len(proxy_updates)}, Items updated {len(item_updates)}",
		user=frappe.session.user,
	)


def compute_afn_stock_updates(current_rows, value_map, field="afn_stock"):
	updates = {}
	for row in current_rows:
		# Names absent from a successful value_map are zeroed, otherwise stale stock lingers.
		new_value = flt(value_map.get(row["name"], 0), 4)
		if flt(row.get(field), 4) == new_value:
			continue
		updates[row["name"]] = {field: new_value}
	return updates


def compute_item_afn_totals(proxy_rows, sku_qty_map, conversion_lookup):
	item_totals = {}
	for proxy_row in proxy_rows:
		new_afn = flt(sku_qty_map.get(proxy_row["name"], 0), 4)
		conversion_factor = flt(conversion_lookup(proxy_row["item"], proxy_row["uom"]))
		base_qty = new_afn * conversion_factor
		item_totals[proxy_row["item"]] = flt(item_totals.get(proxy_row["item"], 0) + base_qty, 4)
	return item_totals
