// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.query_reports['Amazon Go-Live Data Readiness'] = {
	filters: [
		{
			fieldname: 'only_issues',
			label: __('Only Issues'),
			fieldtype: 'Check',
			default: 1,
		},
		{
			fieldname: 'is_fba',
			label: __('Is FBA'),
			fieldtype: 'Select',
			options: '\n0\n1',
		},
	],
	formatter(value, row, column, data, default_formatter) {
		let formatted = default_formatter(value, row, column, data);
		if (
			data &&
			data.status === 'BLOCKED' &&
			['status', 'issues'].includes(column.fieldname)
		) {
			formatted = `<span style="color: var(--red-600)">${formatted}</span>`;
		}
		return formatted;
	},
};
