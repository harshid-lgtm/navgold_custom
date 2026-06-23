// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.query_reports['Amazon Top Selling SKU'] = {
	filters: [
		{
			fieldname: 'from_date',
			label: __('From Date'),
			fieldtype: 'Date',
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
			reqd: 1,
		},
		{
			fieldname: 'to_date',
			label: __('To Date'),
			fieldtype: 'Date',
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: 'sort_by',
			label: __('Rank By'),
			fieldtype: 'Select',
			options: ['Revenue', 'Qty'],
			default: 'Revenue',
		},
		{
			fieldname: 'top_n',
			label: __('Show Top N'),
			fieldtype: 'Int',
			default: 50,
		},
		{
			fieldname: 'item_group',
			label: __('Item Group'),
			fieldtype: 'Link',
			options: 'Item Group',
		},
	],
};
