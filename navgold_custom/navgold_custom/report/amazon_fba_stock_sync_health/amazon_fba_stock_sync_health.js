// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.query_reports['Amazon FBA Stock Sync Health'] = {
	filters: [
		{
			fieldname: 'only_in_stock',
			label: __('Only In Stock'),
			fieldtype: 'Check',
			default: 0,
		},
	],
};
