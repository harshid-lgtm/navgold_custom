// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.query_reports['Amazon Order Sync Health'] = {
	filters: [
		{
			fieldname: 'hours_back',
			label: __('Hours Back'),
			fieldtype: 'Int',
			default: 48,
		},
	],
};
