// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.query_reports['Amazon Pricing Push Monitor'] = {
	filters: [
		{
			fieldname: 'hours_back',
			label: __('Hours Back'),
			fieldtype: 'Int',
			default: 48,
		},
		{
			fieldname: 'source',
			label: __('Source'),
			fieldtype: 'Select',
			options: '\nNavgold\nExternal',
		},
		{
			fieldname: 'submission_type',
			label: __('Type'),
			fieldtype: 'Select',
			options: '\nAmazon\nRepricer',
		},
		{
			fieldname: 'status',
			label: __('Status'),
			fieldtype: 'Select',
			options: '\nPending\nSubmitted\nCompleted\nError',
		},
	],
	formatter(value, row, column, data, default_formatter) {
		let formatted = default_formatter(value, row, column, data);
		if (!data) return formatted;
		if (data.status === 'Error' && column.fieldname === 'status') {
			formatted = `<span style="color: var(--red-600)">${formatted}</span>`;
		}
		if (
			column.fieldname === 'minutes_pending' &&
			cint(data.minutes_pending) > 60
		) {
			formatted = `<span style="color: var(--orange-600)">${formatted}</span>`;
		}
		if (column.fieldname === 'source' && data.source === 'External') {
			formatted = `<span style="color: var(--blue-600)">${formatted}</span>`;
		}
		if (column.fieldname === 'invalid' && cint(data.invalid) > 0) {
			formatted = `<span style="color: var(--red-600)">${formatted}</span>`;
		}
		return formatted;
	},
};
