# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder import DocType

from navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku import process_bulk_selling_price_fetch


def execute(filters: dict | None = None):
	# Prepared report: each rebuild first refreshes listing prices from Amazon,
	# then builds the strategy comparison off the freshly-fetched current_selling_price.
	refresh_amazon_listing_prices()
	return get_columns(), get_data(filters)


def refresh_amazon_listing_prices():
	active_proxy_skus = frappe.get_all("Proxy SKU", filters={"is_active": 1}, pluck="name")
	if not active_proxy_skus:
		return

	process_bulk_selling_price_fetch(active_proxy_skus)
	frappe.db.commit()


def get_columns() -> list[dict]:
	def price_column(label, fieldname):
		return {"label": _(label), "fieldname": fieldname, "fieldtype": "Currency", "width": 110}

	return [
		{
			"label": _("Proxy SKU"),
			"fieldname": "proxy_sku",
			"fieldtype": "Link",
			"options": "Proxy SKU",
			"width": 180,
		},
		{
			"label": _("Is FBA"),
			"fieldname": "is_fba",
			"fieldtype": "Check",
			"width": 80,
		},
		price_column("FBA Min", "fba_min"),
		price_column("FBA Max", "fba_max"),
		price_column("SFP Min", "sfp_min"),
		price_column("SFP Max", "sfp_max"),
		price_column("Backorder Min", "backorder_min"),
		price_column("Backorder Max", "backorder_max"),
		price_column("Non-Prime Min", "default_min"),
		price_column("Non-Prime Max", "default_max"),
		{
			"label": _("Current Amazon Price"),
			"fieldname": "current_selling_price",
			"fieldtype": "Currency",
			"width": 150,
		},
	]


def get_data(filters: dict | None = None) -> list[dict]:
	pricing = DocType("Amazon Product Pricing")
	proxy_sku = DocType("Proxy SKU")
	fba = DocType("Amazon Product Strategy").as_("fba")
	sfp = DocType("Amazon Product Strategy").as_("sfp")
	backorder = DocType("Amazon Product Strategy").as_("backorder")
	default = DocType("Amazon Product Strategy").as_("non_prime")

	query = (
		frappe.qb.from_(pricing)
		.left_join(proxy_sku)
		.on(pricing.proxy_sku == proxy_sku.name)
		.left_join(fba)
		.on(pricing.fba_strategy == fba.name)
		.left_join(sfp)
		.on(pricing.sfp_strategy == sfp.name)
		.left_join(backorder)
		.on(pricing.backorder_strategy == backorder.name)
		.left_join(default)
		.on(pricing.default_strategy == default.name)
		.select(
			pricing.proxy_sku,
			proxy_sku.is_fba,
			fba.calculated_min_price.as_("fba_min"),
			fba.calculated_max_price.as_("fba_max"),
			sfp.calculated_min_price.as_("sfp_min"),
			sfp.calculated_max_price.as_("sfp_max"),
			backorder.calculated_min_price.as_("backorder_min"),
			backorder.calculated_max_price.as_("backorder_max"),
			default.calculated_min_price.as_("default_min"),
			default.calculated_max_price.as_("default_max"),
			proxy_sku.current_selling_price,
		)
		.orderby(pricing.proxy_sku)
	)

	if filters and filters.get("proxy_sku"):
		query = query.where(pricing.proxy_sku == filters.get("proxy_sku"))

	return query.run(as_dict=True)
