# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from navgold_custom.navgold_custom.amazon_pricing.price_calculator import get_item_tax_rate

TAX_TEMPLATE_NAME = "TEST-ITEM-TAX-SYNC-20"
TAXED_ITEM_CODE = "TEST-ITEM-TAX-SYNC-TAXED"
UNTAXED_ITEM_CODE = "TEST-ITEM-TAX-SYNC-UNTAXED"
VAT_COST_TYPE = "Test Item Tax Sync VAT"


def create_item_tax_template():
	if frappe.db.exists("Item Tax Template", TAX_TEMPLATE_NAME):
		return TAX_TEMPLATE_NAME

	company = frappe.defaults.get_global_default("company") or frappe.get_all("Company", pluck="name")[0]
	account = frappe.get_all(
		"Account",
		filters={"company": company, "account_type": "Tax", "is_group": 0},
		pluck="name",
		limit=1,
	)
	if not account:
		account = frappe.get_all(
			"Account", filters={"company": company, "is_group": 0}, pluck="name", limit=1
		)

	template = frappe.new_doc("Item Tax Template")
	template.title = TAX_TEMPLATE_NAME
	template.company = company
	template.append("taxes", {"tax_type": account[0], "tax_rate": 20})
	template.insert()
	template.rename(TAX_TEMPLATE_NAME)
	return TAX_TEMPLATE_NAME


def create_item(item_code, tax_template=None):
	if frappe.db.exists("Item", item_code):
		return item_code

	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = item_code
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 0
	if tax_template:
		item.append("taxes", {"item_tax_template": tax_template})
	item.insert()
	return item.name


VAT_SUB_TYPE = f"{VAT_COST_TYPE}-VAT"


def create_vat_cost_type():
	if not frappe.db.exists("Cost Type", VAT_COST_TYPE):
		cost_type = frappe.new_doc("Cost Type")
		cost_type.cost_type_name = VAT_COST_TYPE
		cost_type.calculation_type = "VAT"
		cost_type.insert()
	if not frappe.db.exists("Cost Sub Type", VAT_SUB_TYPE):
		sub_type = frappe.new_doc("Cost Sub Type")
		sub_type.cost_type = VAT_COST_TYPE
		sub_type.sub_type_name = "VAT"
		sub_type.source = "Item-Tax-Sync"
		sub_type.insert()
	return VAT_COST_TYPE


def create_strategy_with_tax_sync(item_code):
	proxy_sku = frappe.new_doc("Proxy SKU")
	proxy_sku.item = item_code
	proxy_sku.uom = "Nos"
	proxy_sku.is_active = 1
	proxy_sku.insert()

	strategy = frappe.new_doc("Amazon Product Strategy")
	strategy.proxy_sku = proxy_sku.name
	strategy.strategy_name = "Tax-Sync"
	strategy.is_active = 1
	strategy.min_margin = 5
	strategy.max_margin = 10
	strategy.append(
		"costs",
		{
			"cost_type": VAT_COST_TYPE,
			"cost_sub_type": VAT_SUB_TYPE,
			"value_mode": "Auto",
			"rank": 1,
			# Bogus value that populate_costs must overwrite from the item's tax template.
			"value": 999,
		},
	)
	strategy.insert()
	return strategy


class TestItemTaxSync(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.tax_template = create_item_tax_template()
		create_vat_cost_type()
		cls.taxed_item = create_item(TAXED_ITEM_CODE, tax_template=cls.tax_template)
		cls.untaxed_item = create_item(UNTAXED_ITEM_CODE)

	def test_get_item_tax_rate_reads_template_rate(self):
		self.assertEqual(get_item_tax_rate(self.taxed_item), 20)

	def test_get_item_tax_rate_defaults_to_zero_without_template(self):
		self.assertEqual(get_item_tax_rate(self.untaxed_item), 0)

	def test_get_item_tax_rate_handles_missing_item(self):
		self.assertEqual(get_item_tax_rate(None), 0)
		self.assertEqual(get_item_tax_rate("___does_not_exist___"), 0)

	def test_populate_costs_pulls_vat_from_item(self):
		strategy = create_strategy_with_tax_sync(self.taxed_item)
		vat_cost = next(cost for cost in strategy.costs if cost.cost_type == VAT_COST_TYPE)
		self.assertEqual(vat_cost.value, 20)
