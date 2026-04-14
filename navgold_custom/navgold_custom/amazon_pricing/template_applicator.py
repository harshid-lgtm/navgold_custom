# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt


import frappe
from frappe import _


@frappe.whitelist()
def apply_template_to_pricing(template_name: str, pricing_name: str) -> dict:
	template = frappe.get_cached_doc("Amazon Strategy Template", template_name)
	pricing = frappe.get_doc("Amazon Product Pricing", pricing_name)

	existing_strategies = frappe.get_all(
		"Amazon Product Strategy", filters={"proxy_sku": pricing.proxy_sku}, pluck="name"
	)

	if existing_strategies:
		frappe.throw(
			_(
				"This Proxy SKU already has {0} strategies. "
				"Delete existing strategies before applying a template."
			).format(len(existing_strategies))
		)

	strategy_map = {}
	for template_strategy in template.strategies:
		strategy = create_strategy_from_template(template_strategy, pricing.proxy_sku)
		strategy_map[template_strategy.strategy_type] = strategy.name

	if "FBA" in strategy_map:
		pricing.fba_strategy = strategy_map["FBA"]
	if "SFP" in strategy_map:
		pricing.sfp_strategy = strategy_map["SFP"]
	if "Backorder" in strategy_map:
		pricing.backorder_strategy = strategy_map["Backorder"]
	if "Default" in strategy_map:
		pricing.default_strategy = strategy_map["Default"]

	pricing.save()

	return {
		"success": True,
		"pricing": pricing_name,
		"strategies_created": len(strategy_map),
		"strategies": strategy_map,
	}


def create_strategy_from_template(template_strategy, proxy_sku: str):
	strategy = frappe.new_doc("Amazon Product Strategy")
	strategy.strategy_name = template_strategy.strategy_name
	strategy.proxy_sku = proxy_sku
	strategy.min_margin = template_strategy.min_margin
	strategy.max_margin = template_strategy.max_margin
	strategy.repricer_rule = template_strategy.repricer_rule
	strategy.is_active = 1

	if template_strategy.cost_set_template:
		cost_template = frappe.get_cached_doc("Amazon Cost Set Template", template_strategy.cost_set_template)
		for cost in cost_template.costs:
			strategy.append(
				"costs",
				{
					"cost_type": cost.cost_type,
					"cost_sub_type": cost.cost_sub_type,
					"value": cost.default_value or 0,
					"is_active": cost.is_active,
					"rank": cost.rank or 1,
					"source": cost.source,
				},
			)

	strategy.insert()
	return strategy


@frappe.whitelist()
def apply_default_template_to_new_pricing(pricing_name: str) -> dict:
	default_template = frappe.db.get_value("Amazon Strategy Template", {"is_default": 1}, "name")

	if not default_template:
		return None

	return apply_template_to_pricing(default_template, pricing_name)


@frappe.whitelist()
def bulk_apply_template(template_name: str, pricing_names: list | None = None) -> dict:
	if isinstance(pricing_names, str):
		pricing_names = frappe.parse_json(pricing_names)

	if not pricing_names:
		pricing_names = frappe.get_all(
			"Amazon Product Pricing",
			filters={
				"is_active": 1,
				"fba_strategy": ["is", "not set"],
				"sfp_strategy": ["is", "not set"],
				"backorder_strategy": ["is", "not set"],
				"default_strategy": ["is", "not set"],
			},
			pluck="name",
		)

	if not pricing_names:
		frappe.throw(_("No pricing records found to apply template"))

	frappe.enqueue(
		run_bulk_apply_template,
		template_name=template_name,
		pricing_names=pricing_names,
		queue="long",
		timeout=6000,
		job_name="Bulk Apply Strategy Template",
	)

	frappe.msgprint(_("Bulk template application has been queued. You will be notified on completion."))
	return {"queued": True, "count": len(pricing_names)}


def run_bulk_apply_template(template_name: str, pricing_names: list):
	success = 0
	errors = []

	for pricing_name in pricing_names:
		try:
			apply_template_to_pricing(template_name, pricing_name)
			success += 1
		except Exception as error:
			errors.append({"pricing": pricing_name, "error": str(error)})
			frappe.log_error(
				f"Template application error for {pricing_name}: {error!s}", "Bulk Template Application"
			)

	frappe.publish_realtime(
		"bulk_template_complete",
		{"success_count": success, "error_count": len(errors)},
	)
