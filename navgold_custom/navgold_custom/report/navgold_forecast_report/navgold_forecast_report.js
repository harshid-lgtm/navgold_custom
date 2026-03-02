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

		report.page.add_inner_button(__('Create Material Request'), () => {
			const selected_rows = report.get_checked_items();

			if (selected_rows.length === 0) {
				frappe.msgprint(__('Please select at least one row'));
				return;
			}

			const items_with_qty =
				forecast_report_state.get_items_with_edited_quantities(selected_rows);

			const valid_items = items_with_qty.filter(
				(r) => parseFloat(r.quantity_to_transfer) > 0,
			);

			if (valid_items.length === 0) {
				frappe.msgprint(
					__(
						'No selected rows have a Quantity to Transfer > 0. Please edit the quantity first.',
					),
				);
				return;
			}

			frappe.confirm(
				__('Create Material Request for {0} items?', [valid_items.length]),
				async () => {
					try {
						const response = await frappe.call({
							method:
								'navgold_custom.navgold_custom.report.navgold_forecast_report.navgold_forecast_report.create_material_requests_from_forecast_report',
							args: {
								selected_items: valid_items,
							},
							freeze: true,
							freeze_message: __('Creating Material Request...'),
						});

						if (response.message?.success) {
							const mr_name = response.message.material_request;
							frappe.msgprint({
								title: __('Material Request Created'),
								message: __(
									'Created: <a href="/app/material-request/{0}" target="_blank">{0}</a>',
									[mr_name],
								),
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
	},

	get_datatable_options(options) {
		return Object.assign(options, {
			checkboxColumn: true,
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

			// Update display in-place
			const wrapper = button.closest('div');
			if (wrapper) {
				const span = wrapper.querySelector('span:first-child');
				if (span) span.textContent = new_qty;
				button.setAttribute('data-qty', new_qty);
			}

			// Auto-check the row
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
