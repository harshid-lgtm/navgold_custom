// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt
frappe.ui.form.on('Pick List', {
	refresh(frm) {
		frm.add_custom_button(__('Get Items by Weight'), () => {
			frm.events.open_weight_based_selection_dialog(frm);
		});

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
							'erpnext.stock.doctype.pick_list.pick_list.create_stock_entry',
						args: { pick_list: frm.doc },
					});
					const stock_entry = response.message;
					stock_entry.stock_entry_type = 'FBA Transfer';
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
					'navgold_custom.navgold_custom.custom_script.pick_list.pick_list.get_open_sales_orders_for_delivery',
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
						<th>Delivery Date</th>
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
            <td>${frappe.datetime.str_to_user(so.delivery_date) || '-'}</td>
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
					'navgold_custom.navgold_custom.custom_script.pick_list.pick_list.map_sales_orders_to_pick_list',
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

				frappe.show_alert({
					message: __('Items added from {0} Sales Orders', [
						selected_orders.length,
					]),
					indicator: 'green',
				});
			}
		} catch (err) {
			frappe.msgprint(__('Failed to add items from Sales Orders'));
		}
	},
});
