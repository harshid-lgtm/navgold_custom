// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.query_reports['Amazon Strategy Price Comparison'] = {
	filters: [
		{
			fieldname: 'proxy_sku',
			label: __('Proxy SKU'),
			fieldtype: 'Link',
			options: 'Proxy SKU',
		},
	],
};
