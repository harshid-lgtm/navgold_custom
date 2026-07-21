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
			fieldname: 'status',
			label: __('Status'),
			fieldtype: 'Select',
			options: '\nBlocked\nUnblocked',
		},
		{
			fieldname: 'refresh_strategies',
			label: __('Refresh Strategies (Live Preview only)'),
			fieldtype: 'Check',
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		if (column.fieldname === 'unblock') {
			if (!data || !cint(data.can_unblock)) {
				return '';
			}

			return `<a class="unblock-price-deviation" data-proxy-sku="${
				data.proxy_sku
			}">${__('Unblock')}</a>`;
		}

		const formatted = default_formatter(value, row, column, data);

		if (column.fieldname === 'status' && data && data.status === 'Blocked') {
			return `<span style="color: var(--red-500); font-weight: 600;">${formatted}</span>`;
		}

		return formatted;
	},

	onload(report) {
		$(report.page.wrapper).on(
			'click',
			'.unblock-price-deviation',
			async function () {
				const proxy_sku = $(this).attr('data-proxy-sku');
				const row = report.data.find((data) => data.proxy_sku === proxy_sku);
				if (!row) {
					return;
				}

				await prompt_and_unblock(report, row);
			},
		);
	},
};

async function prompt_and_unblock(report, row) {
	const min_price = flt(row.min_price);
	const max_price = flt(row.max_price);
	const current_price = flt(row.current_price);

	let suggested_price = current_price;
	if (!current_price || current_price > max_price) {
		suggested_price = max_price;
	} else if (current_price < min_price) {
		suggested_price = min_price;
	}

	const values = await new Promise((resolve) => {
		frappe.prompt(
			[
				{
					fieldname: 'new_selling_price',
					label: __('Current Selling Price on Amazon'),
					fieldtype: 'Currency',
					reqd: 1,
					default: suggested_price,
					description: __(
						'Must be within the band {0} - {1}, otherwise {2} stays blocked.',
						[
							format_currency(min_price),
							format_currency(max_price),
							row.proxy_sku,
						],
					),
				},
			],
			resolve,
			__('Unblock {0}', [row.proxy_sku]),
			__('Unblock'),
		);
	});

	await frappe.call({
		method:
			'navgold_custom.navgold_custom.amazon_pricing.price_guard.unblock_price_deviation',
		args: {
			proxy_sku: row.proxy_sku,
			new_selling_price: values.new_selling_price,
		},
	});

	frappe.show_alert({
		message: __('{0} unblocked', [row.proxy_sku]),
		indicator: 'green',
	});
	report.refresh();
}
