// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt
frappe.ui.form.on('Pick List', {
	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__('Get Items by Weight'), () => {
				frm.events.open_weight_based_selection_dialog(frm);
			});
		}

		add_scan_buttons(frm);
		setup_delivery_note_buttons(frm);

		if (
			frm.doc.docstatus === 1 &&
			frm.doc.purpose === 'Material Transfer' &&
			frm.doc.status !== 'Completed'
		) {
			frm.remove_custom_button(__('Stock Entry'), __('Create'));
			frm.add_custom_button(
				__('Stock Entry'),
				async () => {
					const response = await frappe.call({
						method:
							'navgold_custom.navgold_custom.custom_script.pick_list.pick_list_api.create_fba_transfer_stock_entry',
						args: { pick_list_name: frm.doc.name },
						freeze: true,
						freeze_message: __('Creating FBA Transfer Stock Entry...'),
					});
					const stock_entry = response.message;
					frappe.model.sync(stock_entry);
					frappe.set_route('Form', 'Stock Entry', stock_entry.name);
				},
				__('Create'),
			);
		}
	},

	async open_weight_based_selection_dialog(frm) {
		if (!frm.doc.company) {
			frappe.throw({
				title: __('Mandatory'),
				message: __('Please select a Company before getting items'),
				indicator: 'red',
			});
		}

		await frm.events.fetch_and_show_sales_orders(frm);
	},

	async fetch_and_show_sales_orders(frm) {
		try {
			const response = await frappe.call({
				method:
					'navgold_custom.navgold_custom.custom_script.pick_list.pick_list_api.get_open_sales_orders_for_delivery',
				args: {
					customer: frm.doc.customer || null,
					company: frm.doc.company,
				},
			});

			if (!response.message || response.message.length === 0) {
				frappe.msgprint(__('No open Sales Orders with pending items found'));
				return;
			}

			const weight_limit = await frappe.db.get_single_value(
				'Navgold Settings',
				'tote_weight_limit',
			);

			frm.events.show_sales_order_selection_popup(
				frm,
				response.message,
				weight_limit,
			);
		} catch (err) {
			frappe.msgprint(__('Error fetching Sales Orders'));
		}
	},

	show_sales_order_selection_popup(frm, sales_orders, weight_limit) {
		const dialog = new frappe.ui.Dialog({
			title: __('Select Sales Orders by Weight'),
			size: 'extra-large',
			fields: [
				{
					fieldname: 'weight_limit_display',
					label: __('Weight Limit (In grams)'),
					fieldtype: 'Data',
					read_only: 1,
					default: weight_limit,
				},
				{
					fieldname: 'selected_weight_display',
					label: __('Selected Total Weight (In grams)'),
					fieldtype: 'Data',
					read_only: 1,
					default: '0.00',
				},
				{
					fieldname: 'sales_orders_table',
					label: __('Sales Orders'),
					fieldtype: 'HTML',
				},
			],
			primary_action_label: __('Get Items'),
			primary_action: async (values) => {
				const selected = [];
				dialog.$wrapper
					.find('input[data-sales-order]:checked')
					.each(function () {
						selected.push($(this).data('sales-order'));
					});

				if (selected.length === 0) {
					frappe.msgprint(__('Please select at least one Sales Order'));
					return;
				}

				const selected_weight = parseFloat(
					dialog.get_value('selected_weight_display') || 0,
				);

				if (selected_weight > weight_limit) {
					frappe.confirm(
						__(
							'Selected weight ({0} g) exceeds the limit ({1} g). Do you want to proceed?',
							[selected_weight.toFixed(2), weight_limit],
						),
						() => {
							frm.events.map_selected_orders_to_picklist(frm, selected, dialog);
						},
					);
				} else {
					await frm.events.map_selected_orders_to_picklist(
						frm,
						selected,
						dialog,
					);
				}
			},
		});

		let html = `
			<table class="table table-bordered">
				<thead>
					<tr>
						<th style="width: 50px;"><input type="checkbox" id="select-all-so"></th>
						<th>Sales Order</th>
						<th>Customer</th>
						<th>Dispatch Date</th>
						<th>Delivery Date</th>
						<th>Is Prime</th>
						<th>Total Weight (g)</th>
						<th>Pending Items</th>
					</tr>
				</thead>
				<tbody>
		`;

		for (const so of sales_orders) {
			html += `
        <tr>
            <td>
                <input type="checkbox"
                       class="so-checkbox"
                       data-sales-order="${so.sales_order}"
                       data-weight="${so.total_weight}">
            </td>
            <td><a href="/app/sales-order/${so.sales_order}" target="_blank">
                ${so.sales_order}
            </a></td>
            <td>${so.customer}</td>
            <td>${frappe.datetime.str_to_user(so.dispatch_date) || '-'}</td>
            <td>${frappe.datetime.str_to_user(so.delivery_date) || '-'}</td>
            <td>${
							cint(so.is_prime)
								? '<span class="indicator-pill blue">Prime</span>'
								: '-'
						}</td>
            <td>${flt(so.total_weight, 2)}</td>
            <td>${so.pending_items_count}</td>
        </tr>
    `;
		}

		html += `
				</tbody>
			</table>
		`;

		dialog.fields_dict.sales_orders_table.$wrapper.html(html);

		dialog.$wrapper.on('change', '.so-checkbox', () => {
			let total_weight = 0;
			dialog.$wrapper.find('.so-checkbox:checked').each(function () {
				total_weight += parseFloat($(this).data('weight') || 0);
			});

			dialog.set_value('selected_weight_display', total_weight.toFixed(2));

			const color_class =
				total_weight > weight_limit ? 'text-danger' : 'text-success';
			dialog.fields_dict.selected_weight_display.$wrapper
				.find('.control-value')
				.removeClass('text-danger text-success')
				.addClass(color_class);
		});

		dialog.$wrapper.on('change', '#select-all-so', function () {
			const checked = $(this).is(':checked');
			dialog.$wrapper
				.find('.so-checkbox')
				.prop('checked', checked)
				.trigger('change');
		});

		dialog.show();
	},

	async map_selected_orders_to_picklist(frm, selected_orders, dialog) {
		try {
			const response = await frappe.call({
				method:
					'navgold_custom.navgold_custom.custom_script.pick_list.pick_list_api.map_sales_orders_to_pick_list',
				args: {
					sales_order_names: JSON.stringify(selected_orders),
					target_pr_doc_json: JSON.stringify(frm.doc),
				},
				freeze: true,
				freeze_message: __('Adding items from Sales Orders...'),
			});

			if (response.message) {
				frappe.model.sync(response.message);
				frm.refresh_field('locations');

				dialog.hide();

				const picked_orders = new Set(
					(response.message.locations || [])
						.map((row) => row.sales_order)
						.filter(Boolean),
				);

				frappe.show_alert({
					message: __('Items added from {0} Sales Orders', [
						picked_orders.size,
					]),
					indicator: 'green',
				});
			}
		} catch (err) {
			frappe.msgprint(__('Failed to add items from Sales Orders'));
		}
	},
});

function setup_delivery_note_buttons(frm) {
	if (frm.doc.docstatus !== 1) return;

	frm.remove_custom_button(__('Delivery Note'), __('Create'));

	const sales_orders = [
		...new Set(
			(frm.doc.locations || []).map((row) => row.sales_order).filter(Boolean),
		),
	];
	if (!sales_orders.length) return;

	frm.add_custom_button(__('Delivery Note'), () => {
		frappe.set_route('List', 'Delivery Note', {
			'Delivery Note Item.against_sales_order': ['in', sales_orders],
			docstatus: ['!=', 2],
		});
	});
}

function add_scan_buttons(frm) {
	if (frm.doc.docstatus !== 0) return;
	if (!(frm.doc.locations || []).length) return;

	if (!frm.doc.custom_tote) {
		frm.add_custom_button(__('Scan Tote'), () =>
			navgold.packing.open_scan_tote_dialog(frm, 'custom_tote', false),
		);
	}

	if (frm.doc.custom_tote) {
		frm.add_custom_button(__('Scan Item'), () =>
			navgold.packing.open_scan_item_dialog(frm, process_pick_scan, false),
		);
	}
}

async function process_pick_scan(frm, barcode, expected_item_code = null) {
	try {
		const scan_result = await navgold.packing.resolve_barcode(barcode);

		if (scan_result?.error) {
			frappe.show_alert({ message: scan_result.error, indicator: 'red' }, 5);
			return;
		}

		const item_code = scan_result.item_code;

		if (expected_item_code && item_code !== expected_item_code) {
			frappe.show_alert(
				{
					message: __('Invalid item scanned. Expected {0}, got {1}', [
						expected_item_code,
						item_code,
					]),
					indicator: 'red',
				},
				5,
			);
			return;
		}

		const matching_rows = (frm.doc.locations || []).filter(
			(row) =>
				row.item_code === item_code && flt(row.picked_qty) < flt(row.stock_qty),
		);

		if (!matching_rows.length) {
			frappe.show_alert(
				{
					message: __('Item {0} not found or already fully picked', [
						item_code,
					]),
					indicator: 'red',
				},
				5,
			);
			return;
		}

		if (matching_rows.length === 1) {
			show_pick_qty_dialog(frm, matching_rows[0]);
		} else {
			show_row_selection_dialog(frm, matching_rows);
		}
	} catch {
		frappe.show_alert(
			{ message: __('Error scanning barcode'), indicator: 'red' },
			5,
		);
	}
}

function show_row_selection_dialog(frm, rows) {
	const options = rows.map((row) => {
		const warehouse = row.warehouse || __('No Warehouse');
		const sales_order = row.sales_order || __('No SO');
		const remaining = flt(row.stock_qty) - flt(row.picked_qty);
		return `${warehouse} | ${sales_order} (${__('Remaining')}: ${remaining} ${
			row.stock_uom
		})`;
	});

	const label_to_row = new Map();
	for (const [index, label] of options.entries()) {
		label_to_row.set(label, rows[index]);
	}

	const dialog = new frappe.ui.Dialog({
		title: __('Select Row to Pick'),
		fields: [
			{
				fieldname: 'selected_row',
				fieldtype: 'Select',
				label: __('Warehouse | Sales Order'),
				options: options.join('\n'),
				reqd: 1,
				default: options[0],
			},
		],
		primary_action_label: __('Select'),
		primary_action({ selected_row }) {
			dialog.hide();
			const row = label_to_row.get(selected_row);
			show_pick_qty_dialog(frm, row);
		},
	});
	dialog.show();
}

function pick_info_fields(row) {
	return [
		{
			fieldname: 'item_code',
			fieldtype: 'Data',
			label: __('Item Code'),
			read_only: 1,
			default: row.item_code,
		},
		{
			fieldname: 'description',
			fieldtype: 'Small Text',
			label: __('Description'),
			read_only: 1,
			default: row.description,
		},
		{
			fieldname: 'uom',
			fieldtype: 'Data',
			label: __('UOM'),
			read_only: 1,
			default: row.uom,
		},
		{
			fieldname: 'warehouse',
			fieldtype: 'Data',
			label: __('Warehouse'),
			read_only: 1,
			default: row.warehouse,
		},
		{ fieldname: 'column_break_1', fieldtype: 'Column Break' },
		{
			fieldname: 'required_qty',
			fieldtype: 'Data',
			label: __('Required Qty ({0})', [row.stock_uom]),
			read_only: 1,
			default: cstr(flt(row.stock_qty)),
		},
		{
			fieldname: 'already_picked',
			fieldtype: 'Data',
			label: __('Already Picked ({0})', [row.stock_uom]),
			read_only: 1,
			default: cstr(flt(row.picked_qty)),
		},
	];
}

function show_pick_qty_dialog(frm, row) {
	const current_picked = flt(row.picked_qty);
	const required_qty = flt(row.stock_qty);
	const remaining = required_qty - current_picked;

	const dialog = new frappe.ui.Dialog({
		title: __('Pick Qty'),
		fields: [
			...pick_info_fields(row),
			{
				fieldname: 'qty',
				fieldtype: 'Float',
				label: __('Qty to Pick ({0})', [row.stock_uom]),
				default: remaining > 0 ? remaining : 1,
				reqd: 1,
			},
		],
		primary_action_label: __('Confirm'),
		async primary_action({ qty }) {
			const new_picked = current_picked + flt(qty);

			if (new_picked > required_qty) {
				frappe.show_alert(
					{
						message: __('Picked qty ({0}) exceeds required qty ({1}) {2}', [
							new_picked,
							required_qty,
							row.stock_uom,
						]),
						indicator: 'orange',
					},
					5,
				);
			}

			await frappe.model.set_value(
				row.doctype,
				row.name,
				'picked_qty',
				new_picked,
			);
			dialog.hide();

			try {
				await frm.save();
			} catch {
				frappe.show_alert(
					{ message: __('Failed to save Pick List'), indicator: 'red' },
					5,
				);
				return;
			}

			frappe.show_alert({
				message: __('Picked {0}: {1}/{2} {3}', [
					row.item_code,
					new_picked,
					required_qty,
					row.stock_uom,
				]),
				indicator: 'green',
			});

			const next_row = get_next_pending_row(frm, row);
			if (next_row) {
				show_pick_summary_dialog(frm, next_row);
			} else {
				frappe.show_alert(
					{ message: __('All items have been picked!'), indicator: 'green' },
					7,
				);
			}
		},
	});
	dialog.show();
	setTimeout(() => dialog.fields_dict.qty.$input.select(), 150);
}

function get_next_pending_row(frm, current_row) {
	const rows = frm.doc.locations || [];
	const start = rows.findIndex((r) => r.name === current_row.name) + 1;
	for (let i = 0; i < rows.length; i++) {
		const r = rows[(start + i) % rows.length];
		if (flt(r.picked_qty) < flt(r.stock_qty)) return r;
	}
}

function show_pick_summary_dialog(frm, row) {
	const dialog = new frappe.ui.Dialog({
		title: __('Next Item to Pick'),
		fields: pick_info_fields(row),
		primary_action_label: __('Scan Item'),
		primary_action() {
			dialog.hide();
			navgold.packing.open_scan_item_dialog(
				frm,
				(_frm, barcode) => process_pick_scan(_frm, barcode, row.item_code),
				false,
			);
		},
	});
	dialog.show();
}
