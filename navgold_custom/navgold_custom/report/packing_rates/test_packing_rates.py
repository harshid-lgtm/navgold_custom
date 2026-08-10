# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, get_datetime, today
from navgold_custom.navgold_custom.report.packing_rates.packing_rates import execute

from navgold_custom.navgold_custom.custom_script.pick_list.test_delivery_consolidation import (
	create_pack_item,
	create_pack_sales_order,
	create_test_warehouse,
	get_draft_delivery_note,
	receive_stock,
	submit_pick_list_for,
)

PACKER_A = "test-packer-a@example.com"
PACKER_B = "test-packer-b@example.com"
ITEM_CODE = "TEST-PR-PACK-ITEM"
PARCEL_TEMPLATE = "Test PR Parcel"
TOTE = "TEST-PR-TOTE"


def create_parcel_template():
	if frappe.db.exists("Shipment Parcel Template", PARCEL_TEMPLATE):
		return PARCEL_TEMPLATE

	template = frappe.new_doc("Shipment Parcel Template")
	template.parcel_template_name = PARCEL_TEMPLATE
	template.length = 10
	template.width = 10
	template.height = 10
	template.weight = 1
	template.insert()
	return template.name


def create_packer(email, full_name):
	if frappe.db.exists("User", email):
		return email

	user = frappe.new_doc("User")
	user.email = email
	user.first_name = full_name
	user.insert(ignore_permissions=True)
	return user.name


def submit_packing_slip(delivery_note):
	packing_slip = frappe.new_doc("Packing Slip")
	packing_slip.delivery_note = delivery_note.name
	packing_slip.from_case_no = 1
	packing_slip.custom_tote = TOTE
	packing_slip.custom_shipment_parcel_template = create_parcel_template()
	for item in delivery_note.items:
		packing_slip.append(
			"items",
			{
				"item_code": item.item_code,
				"qty": item.qty,
				"custom_packed_qty": item.qty,
				"dn_detail": item.name,
			},
		)
	packing_slip.save()
	packing_slip.submit()
	return packing_slip.name


def stamp(packing_slip, packed_at, packer, net_weight, docstatus=1):
	"""The report buckets on creation/owner, which the framework owns, so a test that wants a
	specific day, packer and weight has to write them after the fact."""
	frappe.db.set_value(
		"Packing Slip",
		packing_slip,
		{
			"creation": packed_at,
			"owner": packer,
			"net_weight_pkg": net_weight,
			"docstatus": docstatus,
		},
		update_modified=False,
	)


def at(day, hour):
	return add_to_date(get_datetime(f"{day} 00:00:00"), hours=hour)


def rows_by_packer(data, packer, pack_date=None):
	return [
		row
		for row in data
		if row["packer"] == packer and (pack_date is None or str(row["pack_date"]) == str(pack_date))
	]


class TestPackingRates(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.shelf = create_test_warehouse("Test PR Shelf")
		cls.consolidation_warehouse = create_test_warehouse("Test PR Ready to Ship")
		frappe.db.set_value("UOM", "Nos", "must_be_whole_number", 0)
		frappe.db.set_single_value(
			"Navgold Settings",
			"delivery_consolidation_warehouse",
			cls.consolidation_warehouse,
		)
		frappe.db.set_single_value("Stock Settings", "enable_stock_reservation", 1)

		create_packer(PACKER_A, "Packer A")
		create_packer(PACKER_B, "Packer B")
		create_pack_item(ITEM_CODE)
		receive_stock(ITEM_CODE, cls.shelf, 200)

		cls.slips = [cls.build_slip() for _ in range(5)]

	@classmethod
	def build_slip(cls):
		sales_order = create_pack_sales_order(ITEM_CODE)
		submit_pick_list_for(sales_order)
		delivery_note = get_draft_delivery_note(sales_order)
		return submit_packing_slip(delivery_note)

	def setUp(self):
		super().setUp()
		self.today = today()
		self.yesterday = add_days(self.today, -1)
		# Packer A: three slips spanning 07:00 -> 11:00 today, weights 100/200/300
		stamp(self.slips[0], at(self.today, 7), PACKER_A, 100)
		stamp(self.slips[1], at(self.today, 9), PACKER_A, 200)
		stamp(self.slips[2], at(self.today, 11), PACKER_A, 300)
		# Packer B: a lone slip today, so there is no span to divide by
		stamp(self.slips[3], at(self.today, 8), PACKER_B, 500)
		# Packer A again, yesterday, to exercise the Required Days window
		stamp(self.slips[4], at(self.yesterday, 9), PACKER_A, 400)

	def test_groups_by_day_and_packer(self):
		_, data = execute({"required_days": 1})

		self.assertEqual(len(rows_by_packer(data, PACKER_A, self.today)), 1)
		self.assertEqual(len(rows_by_packer(data, PACKER_B, self.today)), 1)
		self.assertEqual(len(rows_by_packer(data, PACKER_A, self.yesterday)), 1)

	def test_orders_packed_counts_slips_in_the_group(self):
		_, data = execute({"required_days": 1})

		row = rows_by_packer(data, PACKER_A, self.today)[0]
		self.assertEqual(row["orders_packed"], 3)

	def test_rate_divides_by_the_first_to_last_span(self):
		_, data = execute({"required_days": 1})

		row = rows_by_packer(data, PACKER_A, self.today)[0]
		self.assertEqual(row["hours"], 4.0)
		self.assertEqual(row["orders_packed_per_hour"], 0.75)

	def test_a_single_slip_day_reports_no_rate(self):
		_, data = execute({"required_days": 1})

		row = rows_by_packer(data, PACKER_B, self.today)[0]
		self.assertEqual(row["hours"], 0)
		self.assertIsNone(row["orders_packed_per_hour"])

	def test_avg_weight_averages_the_packed_weight(self):
		_, data = execute({"required_days": 1})

		row = rows_by_packer(data, PACKER_A, self.today)[0]
		self.assertEqual(row["avg_weight"], 200)

	def test_packer_name_resolves_to_the_user_full_name(self):
		_, data = execute({"required_days": 1})

		row = rows_by_packer(data, PACKER_A, self.today)[0]
		self.assertEqual(row["packer_name"], "Packer A")

	def test_required_days_zero_covers_today_only(self):
		_, data = execute({"required_days": 0})

		self.assertEqual(len(rows_by_packer(data, PACKER_A, self.today)), 1)
		self.assertEqual(rows_by_packer(data, PACKER_A, self.yesterday), [])

	def test_required_days_one_reaches_back_to_yesterday(self):
		_, data = execute({"required_days": 1})

		row = rows_by_packer(data, PACKER_A, self.yesterday)[0]
		self.assertEqual(row["orders_packed"], 1)

	def test_missing_filter_defaults_to_today_only(self):
		_, data = execute({})

		self.assertEqual(rows_by_packer(data, PACKER_A, self.yesterday), [])

	def test_draft_and_cancelled_slips_are_left_out(self):
		stamp(self.slips[2], at(self.today, 11), PACKER_A, 300, docstatus=0)
		stamp(self.slips[1], at(self.today, 9), PACKER_A, 200, docstatus=2)

		_, data = execute({"required_days": 1})

		row = rows_by_packer(data, PACKER_A, self.today)[0]
		self.assertEqual(row["orders_packed"], 1)
