// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.query_reports['Amazon Pricing Feed Preview'] = {
	filters: [
		{
			fieldname: 'source',
			label: __('Source'),
			fieldtype: 'Select',
			options: 'Live Preview\nLast Sent',
			default: 'Live Preview',
			reqd: 1,
		},
		{
			fieldname: 'channel',
			label: __('Channel'),
			fieldtype: 'Select',
			options: 'Both\nAmazon\nRepricer',
			default: 'Both',
		},
		{
			fieldname: 'proxy_sku',
			label: __('Proxy SKU'),
			fieldtype: 'Link',
			options: 'Proxy SKU',
		},
		{
			fieldname: 'refresh_strategies',
			label: __('Refresh Strategies (Live Preview only)'),
			fieldtype: 'Check',
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);

		if (column.fieldname === 'status' && data && data.status === 'Blocked') {
			return `<span style="color: var(--red-500); font-weight: 600;">${formatted}</span>`;
		}

		return formatted;
	},
};
