// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.query_reports['To Purchase'] = {
	filters: [
		{
			fieldname: 'supplier',
			label: __('Supplier'),
			fieldtype: 'Link',
			options: 'Supplier',
		},
		{
			fieldname: 'is_amazon_pending',
			label: __('Pending Sales Order'),
			fieldtype: 'Check',
		},
	],

	onload: (report) => {
		setup_po_history_handler(report);
		purchase_report_state.init(report);
		add_custom_styles();
		setTimeout(() => {
			create_info_bar(report);
		}, 200);

		report.page.add_inner_button(__('Create Purchase Orders'), async () => {
			const selected_rows = purchase_report_state.get_selected_items();

			if (selected_rows.length === 0) {
				frappe.msgprint(__('Please select at least one row'));
				return;
			}

			frappe.confirm(
				__('Create Purchase Orders for {0} selected items?', [
					selected_rows.length,
				]),
				() => {
					create_purchase_orders(selected_rows, report);
				},
			);
		});
	},

	get_datatable_options(options) {
		return Object.assign(options, {
			checkboxColumn: true,
			editable: false,
			events: {
				onCheckRow: () => {
					purchase_report_state.update_ui();
				},
			},
		});
	},

	formatter: (value, row, column, data, default_formatter) => {
		const default_value = default_formatter(value, row, column, data);

		if (column.fieldname === 'po_history' && data) {
			return `
				<button class="btn btn-xs btn-default btn-po-history"
					data-item-code="${data.item_code}"
					title="${__('View Purchase Order History')}">
					<i class="fa fa-eye"></i>
				</button>
			`;
		}

		if (column.fieldname === 'allocated_qty' && data) {
			const display_qty = purchase_report_state.get_quantity(
				data.item_code,
				data.supplier,
				data.allocated_qty,
			);

			return `
				<div class="qty-edit-wrapper">
					<span>${display_qty}</span>
					<span
						class="edit-qty-icon"
						data-item-code="${data.item_code}"
						data-supplier="${data.supplier}"
						data-allocated="${data.allocated_qty}"
						data-available="${data.available_stock}"
						title="${__('Edit quantity')}"
						onclick="window.edit_quantity(this)"
					>
						${frappe.utils.icon('edit', 'sm')}
					</span>
				</div>
			`;
		}

		return default_value;
	},
};
const purchase_report_state = {
	edited_quantities: {},
	report: null,

	init(report) {
		this.report = report;
		this.edited_quantities = {};
	},

	set_quantity(item_code, supplier, qty) {
		const key = `${item_code}|${supplier}`;
		this.edited_quantities[key] = qty;
		this.update_ui();
	},

	get_quantity(item_code, supplier, default_qty) {
		const key = `${item_code}|${supplier}`;
		return this.edited_quantities[key] ?? default_qty;
	},

	get_selected_items() {
		return this.report?.get_checked_items() || [];
	},

	calculate_total() {
		const selected = this.get_selected_items();
		return selected.reduce((sum, row) => {
			const qty = this.get_quantity(
				row.item_code,
				row.supplier,
				row.allocated_qty || 0,
			);
			const unit_price = row.unit_price || 0;
			return sum + qty * unit_price;
		}, 0);
	},

	get_items_with_edited_quantities(items) {
		return items.map((item) => {
			const key = `${item.item_code}|${item.supplier}`;
			if (this.edited_quantities[key] !== undefined) {
				return {
					...item,
					allocated_qty: this.edited_quantities[key],
				};
			}
			return item;
		});
	},

	reset() {
		this.edited_quantities = {};
		this.update_ui();
	},

	update_ui: frappe.utils.debounce(function () {
		if (!this.report?.info_bar) return;

		const selected = this.get_selected_items();
		const total = this.calculate_total();
		const info_text = this.report.info_bar.querySelector('.info-text');

		if (info_text) {
			info_text.innerHTML = `
				<strong>${__('Selected')}:</strong> ${selected.length} ${__('items')} | 
				<strong>${__('Total Amount')}:</strong> ${format_currency(total)}
			`;
		}
	}, 50),
};

function add_custom_styles() {
	if (document.getElementById('to-purchase-report-styles')) return;

	const style = document.createElement('style');
	style.id = 'to-purchase-report-styles';
	style.innerHTML = `
		.dt-toast {
			display: none !important;
		}
		
		.qty-edit-wrapper {
			display: flex;
			align-items: center;
			justify-content: center;
			gap: 0.5rem;
		}
		
		.edit-qty-icon {
			color: var(--text-muted);
			cursor: pointer;
			transition: color 0.2s ease;
		}
		
		.edit-qty-icon:hover {
			color: var(--text-color);
		}
		
		.purchase-info-bar {

			margin-bottom: 12px;
			display: flex;
			align-items: center;
			justify-content: end;
			padding-left: 4px

		}
		
		.purchase-info-bar .info-text {
			color: var(--text-color);
			font-weight: 500;
			font-size: 14px;
		}

	`;
	document.head.appendChild(style);
}

function create_info_bar(report) {
	const info_bar = $('<div></div>')
		.addClass('purchase-info-bar')
		.html(`
			<div></div>
			<div class="info-text">
				<strong>${__('Selected')}:</strong> 0 ${__('items')} | 
				<strong>${__('Total Amount')}:</strong> ${format_currency(0)}
			</div>
		`);

	const report_container = report.$report;
	const datatable_wrapper = report_container.find('.datatable');

	if (datatable_wrapper.length) {
		datatable_wrapper.before(info_bar);
	}

	report.info_bar = info_bar[0];

	const clear_btn = info_bar.find('#clear-selection-btn');
	clear_btn.on('click', () => {
		if (report.datatable?.rowmanager) {
			report.datatable.rowmanager.checkAll(false);
			purchase_report_state.update_ui();
		}
	});
}

window.edit_quantity = (button) => {
	const item_code = button.getAttribute('data-item-code');
	const supplier = button.getAttribute('data-supplier');
	const allocated_qty = parseFloat(button.getAttribute('data-allocated'));
	const available_stock = parseFloat(button.getAttribute('data-available'));

	const current_qty = purchase_report_state.get_quantity(
		item_code,
		supplier,
		allocated_qty,
	);

	const dialog = new frappe.ui.Dialog({
		title: __('Edit Quantity'),
		fields: [
			{
				fieldtype: 'Data',
				label: __('Item Code'),
				fieldname: 'item_code',
				read_only: 1,
				default: item_code,
			},
			{
				fieldtype: 'Data',
				label: __('Supplier'),
				fieldname: 'supplier',
				read_only: 1,
				default: supplier,
			},
			{
				fieldtype: 'Float',
				label: __('Current Allocated Qty'),
				fieldname: 'current_allocated',
				read_only: 1,
				default: allocated_qty,
			},
			{
				fieldtype: 'Float',
				label: __('Available Stock'),
				fieldname: 'available_stock',
				read_only: 1,
				default: available_stock,
			},
			{
				fieldtype: 'Float',
				label: __('New Quantity'),
				fieldname: 'new_qty',
				reqd: 1,
				default: current_qty,
			},
		],
		primary_action_label: __('Update'),
		primary_action: async (values) => {
			const new_qty = parseFloat(values.new_qty);

			if (Number.isNaN(new_qty) || new_qty <= 0) {
				frappe.msgprint(__('Please enter a valid quantity greater than 0'));
				return;
			}

			if (new_qty > available_stock) {
				const confirmed = await frappe.confirm(
					__(
						'The quantity you entered ({0}) is greater than the available stock ({1}). Are you sure you want to continue?',
						[new_qty, available_stock],
					),
				);

				if (!confirmed) return;
			}

			purchase_report_state.set_quantity(item_code, supplier, new_qty);
			update_quantity_display(item_code, supplier, new_qty);

			dialog.hide();

			frappe.show_alert({
				message: __('Quantity updated to {0}', [new_qty]),
				indicator: 'green',
			});
		},
	});

	dialog.show();
};

function update_quantity_display(item_code, supplier, new_qty) {
	const edit_icon = document.querySelector(
		`span.edit-qty-icon[data-item-code="${item_code}"][data-supplier="${supplier}"]`,
	);

	if (!edit_icon) return;

	const wrapper = edit_icon.closest('.qty-edit-wrapper');
	if (wrapper) {
		const quantity_span = wrapper.querySelector('span:first-child');
		if (quantity_span) {
			quantity_span.textContent = new_qty;
		}
	}

	const row = edit_icon.closest('.dt-row');
	if (row) {
		const checkbox = row.querySelector(
			'.dt-cell--col-0 input[type="checkbox"]',
		);
		if (checkbox && !checkbox.checked) {
			checkbox.click();
		}
	}
}

async function create_purchase_orders(selected_items, report) {
	const items_with_edited_qty =
		purchase_report_state.get_items_with_edited_quantities(selected_items);

	frappe.show_alert({
		message: __('Creating Purchase Orders...'),
		indicator: 'blue',
	});

	try {
		const r = await frappe.call({
			method:
				'navgold_custom.navgold_custom.report.to_purchase.to_purchase.create_purchase_orders_from_report',
			args: {
				selected_items: items_with_edited_qty,
			},
			freeze: true,
			freeze_message: __('Creating Purchase Orders...'),
		});

		if (r.message?.success) {
			frappe.show_alert(
				{
					message: r.message.message,
					indicator: 'green',
				},
				5,
			);

			if (r.message.purchase_orders && r.message.purchase_orders.length > 0) {
				const po_links = r.message.purchase_orders
					.map(
						(po) =>
							`<a href="/app/purchase-order/${po}" target="_blank">${po}</a>`,
					)
					.join(', ');

				frappe.msgprint({
					title: __('Purchase Orders Created'),
					message: __('Successfully created: {0}', [po_links]),
					indicator: 'green',
				});
			}

			purchase_report_state.reset();
			report.refresh();
		}
	} catch (error) {
		frappe.msgprint({
			title: __('Error'),
			message: __(
				'Failed to create Purchase Orders. Please check the error log.',
			),
			indicator: 'red',
		});
	}
}

function setup_po_history_handler(report) {
	$(document).on('click', '.btn-po-history', function (e) {
		e.preventDefault();
		e.stopPropagation();

		const item_code = $(this).data('item-code');
		if (item_code) {
			show_po_history_dialog(item_code);
		}
	});
}

async function show_po_history_dialog(item_code) {
	const dialog = new frappe.ui.Dialog({
		title: __('Purchase Order History: {0}', [item_code]),
		size: 'extra-large',
		fields: [
			{
				fieldtype: 'HTML',
				fieldname: 'po_history_html',
			},
		],
	});

	dialog.show();

	dialog.fields_dict.po_history_html.$wrapper.html(`
		<div class="text-center py-5">
			<div class="spinner-border text-primary" role="status"></div>
			<p class="mt-2">${__('Loading Purchase Order history...')}</p>
		</div>
	`);

	try {
		const r = await frappe.call({
			method:
				'navgold_custom.navgold_custom.report.to_purchase.to_purchase.get_po_history_for_item',
			args: { item_code },
		});

		const data = r.message || [];

		if (data.length === 0) {
			dialog.fields_dict.po_history_html.$wrapper.html(`
				<div class="text-center py-5 text-muted">
					<i class="fa fa-inbox fa-3x mb-3"></i>
					<p>${__('No Purchase Orders found for this item.')}</p>
				</div>
			`);
			return;
		}

		const table_html = build_po_history_table(data);
		dialog.fields_dict.po_history_html.$wrapper.html(table_html);
	} catch (error) {
		dialog.fields_dict.po_history_html.$wrapper.html(`
			<div class="text-center py-5 text-danger">
				<i class="fa fa-exclamation-triangle fa-3x mb-3"></i>
				<p>${__('Failed to load Purchase Order history.')}</p>
			</div>
		`);
	}
}

function build_po_history_table(data) {
	let rows = '';
	let total_qty = 0;
	let total_amount = 0;

	for (const row of data) {
		total_qty += row.qty || 0;
		total_amount += row.amount || 0;

		const status_color = get_status_color(row.status);

		rows += `
			<tr>
				<td>${frappe.datetime.str_to_user(row.transaction_date)}</td>
				<td>
					<a href="/app/purchase-order/${row.purchase_order}" target="_blank">
						${row.purchase_order}
					</a>
				</td>
				<td>
					<a href="/app/supplier/${row.supplier}" target="_blank">
						${row.supplier}
					</a>
				</td>
				<td>
					<a href="/app/item/${row.item_code}" target="_blank">
						${row.item_code}
					</a>
				</td>
				<td><span class="indicator-pill ${status_color}">${row.status}</span></td>
				<td class="text-right">${row.qty}</td>
				<td class="text-right">${format_currency(row.amount)}</td>
			</tr>
		`;
	}

	return `
		<div class="po-history-table-wrapper" style="max-height: 400px; overflow-y: auto;">
			<table class="table table-bordered table-hover">
				<thead class="sticky-top bg-white">
					<tr>
						<th>${__('Date')}</th>
						<th>${__('Purchase Order')}</th>
						<th>${__('Supplier')}</th>
						<th>${__('Item Code')}</th>
						<th>${__('Status')}</th>
						<th class="text-right">${__('Qty')}</th>
						<th class="text-right">${__('Amount')}</th>
					</tr>
				</thead>
				<tbody>
					${rows}
				</tbody>
				<tfoot class="bg-light font-weight-bold">
					<tr>
						<td colspan="5" class="text-right"><strong>${__('Total')}</strong></td>
						<td class="text-right"><strong>${total_qty}</strong></td>
						<td class="text-right"><strong>${format_currency(total_amount)}</strong></td>
					</tr>
				</tfoot>
			</table>
		</div>
	`;
}

function get_status_color(status) {
	const colors = {
		'To Receive and Bill': 'orange',
		'To Bill': 'yellow',
		'To Receive': 'blue',
		'To Pay': 'purple',
		Completed: 'green',
		Closed: 'gray',
		Cancelled: 'red',
	};
	return colors[status] || 'gray';
}
