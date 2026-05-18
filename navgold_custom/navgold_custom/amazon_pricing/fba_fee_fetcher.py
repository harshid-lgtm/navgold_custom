import frappe
from frappe.query_builder import DocType
from frappe.utils.data import flt, now_datetime

from navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api import Reports
from navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku import parse_listings_tsv

REPORT_TYPE = "GET_FBA_ESTIMATED_FBA_FEES_TXT_DATA"
FEE_COST_TYPE = "Fulfillment Cost"
FEE_COST_SUB_TYPE = "Fulfillment Cost-Estimated FBA Fee"
FEE_SOURCE = "Amazon-Sync"
DOMESTIC_FEE_COLUMN = "expected-domestic-fulfilment-fee-per-unit"


@frappe.whitelist()
def bulk_fetch_fba_fees():
	frappe.enqueue(
		"navgold_custom.navgold_custom.amazon_pricing.fba_fee_fetcher.process_fba_fee_fetch",
		queue="long",
		timeout=1800,
	)
	return {"message": "FBA Fee fetch has been queued"}


def process_fba_fee_fetch():
	reports_client = Reports.from_amazon_settings()
	tsv_content = reports_client.request_and_download_report(REPORT_TYPE)

	rows = parse_listings_tsv(tsv_content)
	if not rows:
		frappe.publish_realtime("msgprint", "FBA Fee report returned no data", user=frappe.session.user)
		return

	sku_fee_map = build_sku_fee_map(rows)
	if not sku_fee_map:
		frappe.publish_realtime("msgprint", "No GBP FBA fees found in report", user=frappe.session.user)
		return

	pricing_records = get_fba_pricing_records(list(sku_fee_map.keys()))
	if not pricing_records:
		frappe.publish_realtime(
			"msgprint",
			"No matching Amazon Product Pricing records found for FBA SKUs",
			user=frappe.session.user,
		)
		return

	existing_costs = get_existing_fba_fee_costs([row["fba_strategy"] for row in pricing_records])

	updated = 0
	inserted = 0
	unchanged = 0
	skipped_manual = 0
	failed = 0

	for row in pricing_records:
		strategy_name = row["fba_strategy"]
		fee = sku_fee_map[row["proxy_sku"]]

		try:
			existing_cost = existing_costs.get(strategy_name)
			if existing_cost:
				if existing_cost["source"] != FEE_SOURCE:
					skipped_manual += 1
					continue
				if flt(existing_cost["value"], 4) == flt(fee, 4):
					unchanged += 1
					continue
				strategy = frappe.get_doc("Amazon Product Strategy", strategy_name)
				for cost in strategy.costs:
					if cost.name == existing_cost["name"]:
						cost.value = fee
						cost.is_active = 1
						cost.rank = 1
						break
				strategy.save(ignore_permissions=True)
				updated += 1
			else:
				strategy = frappe.get_doc("Amazon Product Strategy", strategy_name)
				strategy.append(
					"costs",
					{
						"cost_type": FEE_COST_TYPE,
						"cost_sub_type": FEE_COST_SUB_TYPE,
						"value": fee,
						"source": FEE_SOURCE,
						"is_active": 1,
						"rank": 1,
					},
				)
				strategy.save(ignore_permissions=True)
				inserted += 1
		except Exception as e:
			failed += 1
			frappe.log_error(
				f"FBA Fee update failed for strategy {strategy_name}: {e!s}",
				"FBA Fee Fetch",
			)

	frappe.db.commit()  # nosemgrep: frappe-manual-commit -- background job, persist before notifying user

	frappe.publish_realtime(
		"msgprint",
		(
			f"FBA Fee Fetch Complete: Updated {updated}, Inserted {inserted}, "
			f"Unchanged {unchanged}, Skipped Manual {skipped_manual}, Failed {failed}"
		),
		user=frappe.session.user,
	)


def build_sku_fee_map(rows):
	sku_fee_map = {}
	for row in rows:
		if (row.get("currency") or "").strip().upper() != "GBP":
			continue
		sku = (row.get("sku") or "").strip()
		fee_raw = (row.get(DOMESTIC_FEE_COLUMN) or "").strip()
		if not sku or not fee_raw or fee_raw == "--":
			continue
		try:
			fee = float(fee_raw)
		except ValueError:
			continue
		if fee > 0:
			sku_fee_map[sku] = fee
	return sku_fee_map


def get_fba_pricing_records(seller_skus):
	proxy_sku = DocType("Proxy SKU")
	pricing = DocType("Amazon Product Pricing")

	return (
		frappe.qb.from_(pricing)
		.inner_join(proxy_sku)
		.on(proxy_sku.name == pricing.proxy_sku)
		.select(pricing.name, pricing.proxy_sku, pricing.fba_strategy)
		.where(
			(proxy_sku.is_fba == 1)
			& (proxy_sku.is_active == 1)
			& (pricing.is_active == 1)
			& (pricing.fba_strategy.notnull())
			& (proxy_sku.name.isin(seller_skus))
		)
		.run(as_dict=True)
	)


def get_existing_fba_fee_costs(strategy_names):
	if not strategy_names:
		return {}

	cost = DocType("Amazon Product Cost")
	rows = (
		frappe.qb.from_(cost)
		.select(cost.name, cost.parent, cost.value, cost.source)
		.where(
			(cost.parenttype == "Amazon Product Strategy")
			& (cost.parent.isin(strategy_names))
			& (cost.cost_sub_type == FEE_COST_SUB_TYPE)
		)
		.run(as_dict=True)
	)

	result = {}
	for row in rows:
		result[row["parent"]] = {
			"name": row["name"],
			"value": row["value"],
			"source": row["source"],
		}
	return result
