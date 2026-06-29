# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import math
from collections.abc import Callable

import frappe
from frappe.query_builder import DocType, Order
from frappe.utils.data import flt

from navgold_custom.navgold_custom.doctype.supplier_item_price_and_stock.supplier_item_price_and_stock import (
	get_supplier_prices,
)
from navgold_custom.navgold_custom.erpnext_compat import get_product_bundle_items
from navgold_custom.navgold_custom.utils import get_conversion_factor, get_free_qty_map


# Here effective calculation means considering bunles/packs.
def get_po_rates(item_codes: list[str]) -> dict[str, float]:
	if not item_codes:
		return {}

	purchase_order_item = DocType("Purchase Order Item")
	rows = (
		frappe.qb.from_(purchase_order_item)
		.select(
			purchase_order_item.item_code,
			purchase_order_item.rate,
			purchase_order_item.creation,
		)
		.where((purchase_order_item.item_code.isin(item_codes)) & (purchase_order_item.docstatus == 1))
		.orderby(purchase_order_item.creation, order=Order.desc)
	).run(as_dict=True)

	result: dict[str, float] = {}
	for row in rows:
		if row["item_code"] not in result:
			result[row["item_code"]] = flt(row["rate"])
	return result


def calculate_bundle_cost(
	item_code: str,
	components: list[dict],
	lookup_rates_by_item: Callable[[list[str]], dict[str, float]],
	missing_component_rates: list[tuple[str, str]] | None = None,
) -> float:
	component_item_codes = [component["item_code"] for component in components]
	rate_map = lookup_rates_by_item(component_item_codes)
	total = 0.0
	for component in components:
		rate = rate_map.get(component["item_code"])
		if rate is None:
			# Don't log per bundle here: callers that run over many bundles collect the
			# misses and emit one consolidated Error Log via log_missing_bundle_component_rates.
			if missing_component_rates is not None:
				missing_component_rates.append((item_code, component["item_code"]))
			return 0.0
		total += flt(rate) * flt(component["qty"])
	return total


def log_missing_bundle_component_rates(missing_component_rates: list[tuple[str, str]]):
	if not missing_component_rates:
		return

	unique_pairs = sorted(set(missing_component_rates))
	bundle_count = len({bundle for bundle, _ in unique_pairs})
	frappe.log_error(
		title=f"Bundle purchase cost skipped — {bundle_count} bundles with missing component rates",
		message="\n".join(
			f"{bundle}: missing rate for component {component}" for bundle, component in unique_pairs
		),
	)


def calculate_pack_or_bundle_cost(
	item_code: str,
	uom: str,
	lookup_rates_by_item: Callable[[list[str]], dict[str, float]],
	missing_component_rates: list[tuple[str, str]] | None = None,
) -> float:
	if not item_code:
		return 0.0

	components = get_product_bundle_items(item_code)
	if components:
		return calculate_bundle_cost(item_code, components, lookup_rates_by_item, missing_component_rates)

	rate_map = lookup_rates_by_item([item_code])
	base_rate = rate_map.get(item_code)
	if base_rate is None:
		return 0.0
	return flt(base_rate) * get_conversion_factor(item_code, uom)


def get_effective_supplier_purchase_cost(
	item_code: str,
	uom: str,
	missing_component_rates: list[tuple[str, str]] | None = None,
) -> float:
	return calculate_pack_or_bundle_cost(item_code, uom, get_supplier_prices, missing_component_rates)


def get_effective_po_purchase_cost(
	item_code: str,
	uom: str,
	missing_component_rates: list[tuple[str, str]] | None = None,
) -> float:
	return calculate_pack_or_bundle_cost(item_code, uom, get_po_rates, missing_component_rates)


def get_bundle_components(item_codes: list[str]) -> dict[str, list[dict]]:
	if not item_codes:
		return {}

	bundle = DocType("Product Bundle")
	bundle_item = DocType("Product Bundle Item")

	bundle_rows = (
		frappe.qb.from_(bundle)
		.select(bundle.new_item_code, bundle.name)
		.where((bundle.new_item_code.isin(item_codes)) & (bundle.disabled == 0))
	).run(as_dict=True)

	if not bundle_rows:
		return {}

	item_to_bundle = {row["new_item_code"]: row["name"] for row in bundle_rows}

	component_rows = (
		frappe.qb.from_(bundle_item)
		.select(bundle_item.parent, bundle_item.item_code, bundle_item.qty)
		.where(bundle_item.parent.isin(list(item_to_bundle.values())))
	).run(as_dict=True)

	components_by_bundle: dict[str, list[dict]] = {}
	for row in component_rows:
		components_by_bundle.setdefault(row["parent"], []).append(
			{"item_code": row["item_code"], "qty": flt(row["qty"])}
		)

	return {
		item_code: components_by_bundle.get(bundle_name, [])
		for item_code, bundle_name in item_to_bundle.items()
	}


def get_buildable_bundle_qty_map(item_codes: list) -> dict:
	bundle_components_map = get_bundle_components(item_codes)
	if not bundle_components_map:
		return {}

	component_item_codes = list(
		{component["item_code"] for components in bundle_components_map.values() for component in components}
	)
	component_free_qty_map = get_free_qty_map(component_item_codes)

	buildable_map = {}
	for item_code, components in bundle_components_map.items():
		buildable_counts = []
		for component in components:
			per_bundle_qty = flt(component["qty"])
			if per_bundle_qty <= 0:
				continue
			component_stock = flt(component_free_qty_map.get(component["item_code"], {}).get("free_qty", 0))
			buildable_counts.append(math.floor(component_stock / per_bundle_qty))
		buildable_map[item_code] = max(min(buildable_counts), 0) if buildable_counts else 0

	return buildable_map


def calculate_effective_purchase_costs(
	item_uom_pairs: list[tuple[str, str]],
	base_rate_resolver: Callable[[list[str]], dict[str, float]],
) -> dict[tuple[str, str], float]:
	if not item_uom_pairs:
		return {}

	item_codes = list({item_code for item_code, _ in item_uom_pairs if item_code})
	bundle_components_map = get_bundle_components(item_codes)

	all_required_codes = set(item_codes)
	for components in bundle_components_map.values():
		for component in components:
			all_required_codes.add(component["item_code"])

	rate_map = base_rate_resolver(list(all_required_codes))

	result: dict[tuple[str, str], float] = {}
	for item_code, uom in item_uom_pairs:
		components = bundle_components_map.get(item_code)
		if components:
			total = 0.0
			for component in components:
				rate = rate_map.get(component["item_code"])
				if rate is None:
					total = 0.0
					break
				total += flt(rate) * flt(component["qty"])
			result[(item_code, uom)] = total
		else:
			base_rate = rate_map.get(item_code)
			result[(item_code, uom)] = (
				flt(base_rate) * get_conversion_factor(item_code, uom) if base_rate else 0.0
			)

	return result


def get_effective_supplier_purchase_costs(
	item_uom_pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], float]:
	return calculate_effective_purchase_costs(item_uom_pairs, get_supplier_prices)


def get_effective_po_purchase_costs(
	item_uom_pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], float]:
	return calculate_effective_purchase_costs(item_uom_pairs, get_po_rates)
