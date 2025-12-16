// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

let edited_quantities = {};

frappe.query_reports['To Purchase'] = {
	filters: [
		{
			fieldname: 'supplier',
			label: __('Supplier'),
			fieldtype: 'Link',
			options: 'Supplier',
		},
	],
	onload: (report) => {
		edited_quantities = {};
		// To remove 1 row selected msg
		setTimeout(() => {
			const style = document.createElement('style');
			style.innerHTML = `
				.dt-toast {
					display: none !important;
				}
				.qty-edit-wrapper {
				display: flex;
				align-items: center;
				justify-content: center;
				gap: 2rem;
			}

			.edit-qty-icon {
				color: var(--text-muted);
				cursor: pointer;
			}

			.edit-qty-icon:hover {
				color: var(--text-color);
			}
		
			`;
			document.head.appendChild(style);
		}, 100);

		report.page.add_inner_button(__('Create Purchase Orders'), async () => {
			const selected_rows = report.get_checked_items();

			if (selected_rows.length === 0) {
				frappe.msgprint(__('Please select at least one row'));
				return;
			}

			const confirmed = await frappe.confirm(
				__('Create Purchase Orders for {0} selected items?', [
					selected_rows.length,
				]),
			);

			if (confirmed) {
				create_purchase_orders(selected_rows, report);
			}
		});
	},

	get_datatable_options(options) {
		return Object.assign(options, {
			checkboxColumn: true,
			editable: false,
			events: {
				onCheckRow: (row) => {},
			},
		});
	},

	formatter: (value, row, column, data, default_formatter) => {
		default_value = default_formatter(value, row, column, data);

		if (column.fieldname === 'allocated_qty' && data) {
			const key = `${data.item_code}|${data.supplier}`;
			const edited_qty = edited_quantities[key];
			const display_qty =
				edited_qty !== undefined ? edited_qty : data.allocated_qty;

			return `
				<div class="qty-edit-wrapper">
					<span>${display_qty}</span>
					<span
						class="edit-qty-icon cursor-pointer"
						data-item-code="${data.item_code}"
						data-supplier="${data.supplier}"
						data-allocated="${data.allocated_qty}"
						data-available="${data.available_stock_normalized}"
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

window.edit_quantity = (button) => {
	const item_code = button.getAttribute('data-item-code');
	const supplier = button.getAttribute('data-supplier');
	const allocated_qty = parseFloat(button.getAttribute('data-allocated'));
	const available_stock = parseFloat(button.getAttribute('data-available'));

	const key = `${item_code}|${supplier}`;
	const current_qty =
		edited_quantities[key] !== undefined
			? edited_quantities[key]
			: allocated_qty;

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

			save_edited_quantity(key, new_qty);
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

function save_edited_quantity(key, qty) {
	edited_quantities[key] = qty;
}

function update_quantity_display(item_code, supplier, new_qty) {
	const editIcon = document.querySelector(
		`span.edit-qty-icon[data-item-code="${item_code}"][data-supplier="${supplier}"]`,
	);

	if (editIcon) {
		const wrapper = editIcon.closest('.qty-edit-wrapper');
		if (wrapper) {
			const quantitySpan = wrapper.querySelector('span:first-child');
			if (quantitySpan) {
				quantitySpan.textContent = new_qty;
			}
		}

		// Automatically check the checkbox for this row
		const row = editIcon.closest('.dt-row');
		if (row) {
			const checkbox = row.querySelector(
				'.dt-cell--col-0 input[type="checkbox"]',
			);
			if (checkbox && !checkbox.checked) {
				checkbox.click();
			}
		}
	}
}

async function create_purchase_orders(selected_items, report) {
	// Apply edited quantities to selected items
	const items_with_edited_qty = selected_items.map((item) => {
		const key = `${item.item_code}|${item.supplier}`;
		if (edited_quantities[key] !== undefined) {
			return {
				...item,
				allocated_qty: edited_quantities[key],
			};
		}
		return item;
	});

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

			// Reset edited quantities after successful PO creation
			edited_quantities = {};
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
