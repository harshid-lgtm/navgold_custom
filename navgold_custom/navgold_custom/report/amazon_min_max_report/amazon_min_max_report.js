// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.query_reports['Amazon Min Max Report'] = {
	filters: [
		{
			fieldname: 'warehouse',
			label: __('Warehouse'),
			fieldtype: 'Link',
			options: 'Warehouse',
		},
		{
			fieldname: 'min_cover_days',
			label: __('Min Cover Days'),
			fieldtype: 'Int',
			default: 4,
		},
		{
			fieldname: 'max_multiplier',
			label: __('Max Multiplier'),
			fieldtype: 'Int',
			default: 3,
		},
	],
};
