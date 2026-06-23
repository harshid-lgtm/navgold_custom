// Copyright (c) 2026, Navgold and contributors
// For license information, please see license.txt

frappe.query_reports['Amazon Daily Order Stats'] = {
	filters: [
		{
			fieldname: 'from_date',
			label: __('From Date'),
			fieldtype: 'Date',
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
		},
		{
			fieldname: 'to_date',
			label: __('To Date'),
			fieldtype: 'Date',
			default: frappe.datetime.get_today(),
		},
	],
};
