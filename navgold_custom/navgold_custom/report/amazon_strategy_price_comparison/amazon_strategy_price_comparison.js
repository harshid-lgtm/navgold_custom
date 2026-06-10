// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

// Each strategy price cell is paired with its variance cell; when |variance| > 5%
// both cells render red.
const VARIANCE_THRESHOLD = 5;
const PRICE_TO_VARIANCE = {
	fba_min: 'fba_min_var',
	fba_max: 'fba_max_var',
	sfp_min: 'sfp_min_var',
	sfp_max: 'sfp_max_var',
	backorder_min: 'backorder_min_var',
	backorder_max: 'backorder_max_var',
	default_min: 'default_min_var',
	default_max: 'default_max_var',
};

frappe.query_reports['Amazon Strategy Price Comparison'] = {
	filters: [
		{
			fieldname: 'proxy_sku',
			label: __('Proxy SKU'),
			fieldtype: 'Link',
			options: 'Proxy SKU',
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);

		let variance_field = null;
		if (column.fieldname in PRICE_TO_VARIANCE) {
			variance_field = PRICE_TO_VARIANCE[column.fieldname];
		} else if (column.fieldname.endsWith('_var')) {
			variance_field = column.fieldname;
		}

		const deviates =
			variance_field &&
			data &&
			!is_null(data[variance_field]) &&
			Math.abs(flt(data[variance_field])) > VARIANCE_THRESHOLD;

		if (deviates) {
			return `<span style="color: var(--red-500); font-weight: 600;">${formatted}</span>`;
		}

		return formatted;
	},
};
