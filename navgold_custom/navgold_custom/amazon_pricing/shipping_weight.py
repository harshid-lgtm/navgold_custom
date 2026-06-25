# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.utils.data import flt

from navgold_custom.navgold_custom.amazon_pricing.purchase_cost_sources import get_product_bundle_items
from navgold_custom.navgold_custom.utils import get_conversion_factor

# Only weight UOMs we can convert to grams with certainty. Anything else (e.g. a mis-entered
# "Nos") is treated as no-data so the weight is left blank rather than silently wrong.
GRAMS_PER_WEIGHT_UOM = {"Gram": 1.0, "g": 1.0, "Kg": 1000.0, "Kilogram": 1000.0}


def item_weight_in_grams(item_code: str) -> float:
	row = frappe.db.get_value("Item", item_code, ["weight_per_unit", "weight_uom"], as_dict=True)
	if not row:
		return 0.0
	factor = GRAMS_PER_WEIGHT_UOM.get(row.weight_uom)
	if not factor or flt(row.weight_per_unit) <= 0:
		return 0.0
	return flt(row.weight_per_unit) * factor


def get_shipping_weight_g(item_code: str, uom: str) -> float:
	if not item_code:
		return 0.0

	components = get_product_bundle_items(item_code)
	if components:
		total = 0.0
		for component in components:
			grams = item_weight_in_grams(component["item_code"])
			if not grams:
				return 0.0
			total += (
				grams
				* flt(component["qty"])
				* get_conversion_factor(component["item_code"], component["uom"])
			)
		return total

	grams = item_weight_in_grams(item_code)
	if not grams:
		return 0.0
	return grams * get_conversion_factor(item_code, uom)
