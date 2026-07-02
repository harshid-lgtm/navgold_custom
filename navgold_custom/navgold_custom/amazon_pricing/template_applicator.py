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

	strategy_map = link_strategies_to_pricing(pricing, template)

	pricing.save()

	return {
		"success": True,
		"pricing": pricing_name,
		"strategies_created": len(strategy_map),
		"strategies": strategy_map,
	}


def link_strategies_to_pricing(pricing_doc, template) -> dict:
	"""Create strategies for the proxy SKU and set the 4 link fields on the pricing doc.

	Returns the strategy_type -> strategy name map. Does NOT save the pricing doc, the
	caller owns the single insert/save so both the existing-pricing and new-pricing paths
	share one code path.
	"""
	strategy_map = {}
	for template_strategy in template.strategies:
		strategy = create_strategy_from_template(template_strategy, pricing_doc.proxy_sku)
		strategy_map[template_strategy.strategy_type] = strategy.name

	if "FBA" in strategy_map:
		pricing_doc.fba_strategy = strategy_map["FBA"]
	if "SFP" in strategy_map:
		pricing_doc.sfp_strategy = strategy_map["SFP"]
	if "Backorder" in strategy_map:
		pricing_doc.backorder_strategy = strategy_map["Backorder"]
	if "Default" in strategy_map:
		pricing_doc.default_strategy = strategy_map["Default"]

	return strategy_map


def apply_template_to_proxy_sku(template_name: str, proxy_sku: str) -> dict:
	if frappe.db.exists("Amazon Product Strategy", {"proxy_sku": proxy_sku}):
		return {"proxy_sku": proxy_sku, "status": "skipped", "strategies_created": 0}

	template = frappe.get_cached_doc("Amazon Strategy Template", template_name)

	pricing_name = frappe.db.get_value("Amazon Product Pricing", {"proxy_sku": proxy_sku}, "name")
	if pricing_name:
		pricing = frappe.get_cached_doc("Amazon Product Pricing", pricing_name)
	else:
		# Inherit is_active from the Proxy SKU — the action deliberately permits inactive
		# selections, so hardcoding 1 would wrongly activate pricing for an inactive SKU.
		pricing = frappe.new_doc("Amazon Product Pricing")
		pricing.proxy_sku = proxy_sku
		pricing.is_active = frappe.db.get_value("Proxy SKU", proxy_sku, "is_active")

	strategy_map = link_strategies_to_pricing(pricing, template)

	if pricing_name:
		pricing.save()
	else:
		pricing.insert()

	return {"proxy_sku": proxy_sku, "status": "applied", "strategies_created": len(strategy_map)}


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
					"value_mode": "Auto",
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
		user=frappe.session.user,
	)


@frappe.whitelist()
def bulk_apply_template_to_proxy_skus(template_name: str, proxy_sku_names: str) -> dict:
	if isinstance(proxy_sku_names, str):
		proxy_sku_names = frappe.parse_json(proxy_sku_names)

	if not frappe.db.exists("Amazon Strategy Template", template_name):
		frappe.throw(_("Amazon Strategy Template {0} does not exist").format(template_name))

	if not proxy_sku_names:
		frappe.throw(_("No proxy SKUs selected"))

	frappe.enqueue(
		run_bulk_apply_to_proxy_skus,
		template_name=template_name,
		proxy_sku_names=proxy_sku_names,
		queue="long",
		timeout=6000,
		job_name="Bulk Apply Template to Proxy SKUs",
	)

	frappe.msgprint(_("Template application has been queued. You will be notified on completion."))
	return {"queued": True, "count": len(proxy_sku_names)}


def run_bulk_apply_to_proxy_skus(template_name: str, proxy_sku_names: list) -> dict:
	applied = 0
	skipped = 0
	errors = 0

	for proxy_sku in proxy_sku_names:
		try:
			frappe.db.savepoint("apply_template")
			result = apply_template_to_proxy_sku(template_name, proxy_sku)
			if result["status"] == "applied":
				applied += 1
			else:
				skipped += 1
		except Exception as error:
			# Roll back only this item so one failed insert cannot poison the transaction
			# and cascade-fail the rest of the batch.
			frappe.db.rollback(save_point="apply_template")
			errors += 1
			frappe.log_error(
				f"Template application error for Proxy SKU {proxy_sku}: {error!s}",
				"Bulk Apply Template to Proxy SKUs",
			)

	tally = {"applied": applied, "skipped": skipped, "errors": errors}

	frappe.publish_realtime(
		"bulk_template_proxy_sku_complete",
		tally,
		user=frappe.session.user,
	)

	return tally
