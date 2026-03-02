// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.query_reports['Navgold Forecast Report'] = {
	filters: [
		{
			fieldname: 'brand',
			label: __('Brand'),
			fieldtype: 'Link',
			options: 'Brand',
		},
		{
			fieldname: 'strategy',
			label: __('Strategy'),
			fieldtype: 'Select',
			options: '\nFBA\nSFP',
		},
		{
			fieldname: 'comment',
			label: __('Comment'),
			fieldtype: 'Select',
			options: '\nActive\nNo Sales\nNot Listed',
		},
	],
};
