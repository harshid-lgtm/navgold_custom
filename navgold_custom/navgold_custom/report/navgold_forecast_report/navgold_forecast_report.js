// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

const forecast_report_state = {
	edited_quantities: {},
	report: null,

	init(report) {
		this.report = report;
		this.edited_quantities = {};
	},

	set_quantity(sku, qty) {
		this.edited_quantities[sku] = qty;
		this.update_ui();
	},

	get_quantity(sku, default_qty) {
		return this.edited_quantities[sku] ?? default_qty;
	},

	get_items_with_edited_quantities(items) {
		return items.map((item) => {
			if (this.edited_quantities[item.sku] !== undefined) {
				return {
					...item,
					quantity_to_transfer: this.edited_quantities[item.sku],
				};
			}
			return item;
		});
	},

	calculate_total_qty_stock_uom() {
		const selected = this.report?.get_checked_items() || [];
		return selected.reduce((sum, row) => {
			const qty = this.get_quantity(row.sku, row.quantity_to_transfer || 0);
			const multiplier = parseFloat(row.variant_multiplier || 1);
			return sum + parseFloat(qty || 0) * multiplier;
		}, 0);
	},

	update_ui: frappe.utils.debounce(function () {
		if (!this.report?.info_bar) return;

		const selected = this.report.get_checked_items() || [];
		const total_stock_qty = this.calculate_total_qty_stock_uom();
		const info_text = this.report.info_bar.querySelector('.info-text');

		if (info_text) {
			info_text.textContent = `${__('Selected')}: ${selected.length} ${__(
				'items',
			)} | ${__('Total Qty to Order (Singles)')}: ${total_stock_qty}`;
		}
	}, 50),
};

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

	onload: (report) => {
		forecast_report_state.init(report);

		const get_valid_items = () => {
			const selected_rows = report.get_checked_items();
			if (selected_rows.length === 0) {
				frappe.msgprint(__('Please select at least one row'));
				return null;
			}
			const items_with_qty =
				forecast_report_state.get_items_with_edited_quantities(selected_rows);
			const valid_items = items_with_qty.filter(
				(row) => parseFloat(row.quantity_to_transfer) > 0,
			);
			if (valid_items.length === 0) {
				frappe.msgprint(
					__(
						'No selected rows have a Quantity to Transfer > 0. Please edit the quantity first.',
					),
				);
				return null;
			}
			return valid_items;
		};

		report.page.add_inner_button(__('Create Material Request'), () => {
			const valid_items = get_valid_items();
			if (!valid_items) return;

			frappe.confirm(
				__('Create Material Request for {0} items?', [valid_items.length]),
				async () => {
					try {
						const response = await frappe.call({
							method:
								'navgold_custom.navgold_custom.report.navgold_forecast_report.navgold_forecast_report.create_material_requests_from_forecast_report',
							args: { selected_items: valid_items },
							freeze: true,
							freeze_message: __('Creating Material Request...'),
						});

						if (response.message?.success) {
							const mr_name = response.message.material_request;
							frappe.msgprint({
								title: __('Created Successfully'),
								message: `MR: <a href="/app/material-request/${mr_name}" target="_blank">${mr_name}</a>`,
								indicator: 'green',
							});
							forecast_report_state.edited_quantities = {};
							report.refresh();
						}
					} catch (error) {
						frappe.msgprint({
							title: __('Error'),
							message: __(
								'Failed to create Material Request. Check error log.',
							),
							indicator: 'red',
						});
					}
				},
			);
		});

		report.page.add_inner_button(__('Create PO + Material Request'), () => {
			const valid_items = get_valid_items();
			if (!valid_items) return;

			frappe.confirm(
				__('Create Purchase Order(s) and Material Request for {0} items?', [
					valid_items.length,
				]),
				async () => {
					try {
						const response = await frappe.call({
							method:
								'navgold_custom.navgold_custom.report.navgold_forecast_report.navgold_forecast_report.create_purchase_orders_and_material_request_from_forecast_report',
							args: { selected_items: valid_items },
							freeze: true,
							freeze_message: __('Creating PO + Material Request...'),
						});

						if (response.message?.success) {
							const mr_name = response.message.material_request;
							const po_names = response.message.purchase_orders;
							const po_links = po_names
								.map(
									(name) =>
										`<a href="/app/purchase-order/${name}" target="_blank">${name}</a>`,
								)
								.join(', ');
							frappe.msgprint({
								title: __('Created Successfully'),
								message: `MR: <a href="/app/material-request/${mr_name}" target="_blank">${mr_name}</a><br>PO(s): ${po_links}`,
								indicator: 'green',
							});
							forecast_report_state.edited_quantities = {};
							report.refresh();
						}
					} catch (error) {
						frappe.msgprint({
							title: __('Error'),
							message: __(
								'Failed to create PO + Material Request. Check error log.',
							),
							indicator: 'red',
						});
					}
				},
			);
		});
	},

	after_refresh: (report) => {
		if (!report.info_bar) {
			create_forecast_info_bar(report);
		}
		forecast_report_state.update_ui();
	},

	get_datatable_options(options) {
		return Object.assign(options, {
			checkboxColumn: true,
			events: {
				onCheckRow: () => {
					forecast_report_state.update_ui();
				},
			},
		});
	},

	formatter: (value, row, column, data, default_formatter) => {
		const default_value = default_formatter(value, row, column, data);

		if (column.fieldname === 'quantity_to_transfer' && data) {
			const display_qty = forecast_report_state.get_quantity(
				data.sku,
				data.quantity_to_transfer || 0,
			);

			return `
				<div style="display: flex; align-items: center; justify-content: center; gap: 0.5rem;">
					<span>${display_qty}</span>
					<span
						class="edit-forecast-qty"
						data-sku="${data.sku}"
						data-qty="${display_qty}"
						title="${__('Edit quantity')}"
						style="color: var(--text-muted); cursor: pointer;"
						onclick="window.edit_forecast_quantity(this)"
					>
						${frappe.utils.icon('edit', 'sm')}
					</span>
				</div>
			`;
		}

		return default_value;
	},
};

function create_forecast_info_bar(report) {
	const info_bar = $('<div></div>')
		.addClass('forecast-info-bar')
		.html(`
			<div class="info-text">
				<strong>${__('Selected')}:</strong> 0 ${__('items')} |
				<strong>${__('Total Qty to Order')}:</strong> 0
			</div>
		`);

	const datatable_wrapper = report.$report.find('.datatable');
	if (datatable_wrapper.length) {
		datatable_wrapper.before(info_bar);
	}

	report.info_bar = info_bar[0];

	if (!document.getElementById('forecast-report-styles')) {
		const style = document.createElement('style');
		style.id = 'forecast-report-styles';
		style.innerHTML = `
			.forecast-info-bar {
				display: flex;
				justify-content: flex-end;
				align-items: center;
				margin-bottom: 12px;
				padding-left: 4px;
			}
			.forecast-info-bar .info-text {
				color: var(--text-color);
				font-weight: 500;
				font-size: 14px;
			}
		`;
		document.head.appendChild(style);
	}
}

window.edit_forecast_quantity = (button) => {
	const sku = button.getAttribute('data-sku');
	const current_qty = parseFloat(button.getAttribute('data-qty')) || 0;

	const dialog = new frappe.ui.Dialog({
		title: __('Edit Quantity to Transfer'),
		fields: [
			{
				fieldtype: 'Data',
				label: __('SKU'),
				fieldname: 'sku',
				read_only: 1,
				default: sku,
			},
			{
				fieldtype: 'Float',
				label: __('Quantity to Transfer'),
				fieldname: 'qty',
				reqd: 1,
				default: current_qty,
			},
		],
		primary_action_label: __('Update'),
		primary_action: (values) => {
			const new_qty = parseFloat(values.qty);

			if (Number.isNaN(new_qty) || new_qty < 0) {
				frappe.msgprint(__('Please enter a valid quantity'));
				return;
			}

			forecast_report_state.set_quantity(sku, new_qty);

			const wrapper = button.closest('div');
			if (wrapper) {
				const span = wrapper.querySelector('span:first-child');
				if (span) span.textContent = new_qty;
				button.setAttribute('data-qty', new_qty);
			}

			const row_el = button.closest('.dt-row');
			if (row_el) {
				const checkbox = row_el.querySelector(
					'.dt-cell--col-0 input[type="checkbox"]',
				);
				if (checkbox && !checkbox.checked) checkbox.click();
			}

			dialog.hide();
			frappe.show_alert({
				message: __('Quantity updated to {0}', [new_qty]),
				indicator: 'green',
			});
		},
	});

	dialog.show();
};
