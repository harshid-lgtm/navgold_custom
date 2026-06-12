// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.query_reports['Supplier Data Readiness'] = {
	filters: [
		{
			fieldname: 'staleness_days',
			label: __('Staleness Days'),
			fieldtype: 'Int',
			default: 7,
		},
	],
	formatter(value, row, column, data, default_formatter) {
		let formatted = default_formatter(value, row, column, data);
		if (data?.is_stale && column.fieldname === 'days_stale') {
			formatted = `<span style="color: var(--red-600)">${formatted}</span>`;
		}
		return formatted;
	},
};
