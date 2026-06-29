// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.query_reports['Amazon Pricing Push Readiness'] = {
	filters: [
		{
			fieldname: 'only_violations',
			label: __('Only Violations'),
			fieldtype: 'Check',
			default: 1,
		},
		{
			fieldname: 'strategy_name',
			label: __('Strategy'),
			fieldtype: 'Select',
			options: '\nfba\nsfp\nbackorder\ndefault',
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);

		if (column.fieldname === 'violations' && data && data.violations) {
			return `<span style="color: var(--red-500); font-weight: 600;">${formatted}</span>`;
		}

		return formatted;
	},
};
