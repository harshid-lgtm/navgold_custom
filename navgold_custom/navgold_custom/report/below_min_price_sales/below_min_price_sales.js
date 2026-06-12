// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.query_reports['Below Min Price Sales'] = {
	filters: [
		{
			fieldname: 'days_back',
			label: __('Days Back'),
			fieldtype: 'Int',
			default: 3,
		},
		{
			fieldname: 'only_below_min',
			label: __('Only Below Min'),
			fieldtype: 'Check',
			default: 1,
		},
		{
			fieldname: 'minimum_deficit',
			label: __('Minimum Deficit'),
			fieldtype: 'Currency',
		},
	],
	formatter(value, row, column, data, default_formatter) {
		let formatted = default_formatter(value, row, column, data);
		if (data && flt(data.deficit) > 0) {
			formatted = `<span style="color: var(--red-600)">${formatted}</span>`;
		}
		return formatted;
	},
};
