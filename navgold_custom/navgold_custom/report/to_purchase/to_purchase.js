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
	],
	onload: (report) => {
		// To remove 1 row selected msg
		setTimeout(() => {
			const style = document.createElement('style');
			style.innerHTML = `
				.dt-toast {
					display: none !important;
				}
			`;
			document.head.appendChild(style);
		}, 100);

		report.page.add_inner_button(__('Create Purchase Orders'), () => {
			const selected_rows = report.get_checked_items();

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
			editable: true,
			events: {
				onCheckRow: (row) => {},
			},
		});
	},
};

function create_purchase_orders(selected_items, report) {
	frappe.show_alert({
		message: __('Creating Purchase Orders...'),
		indicator: 'blue',
	});

	frappe.call({
		method:
			'navgold_custom.navgold_custom.report.to_purchase.to_purchase.create_purchase_orders_from_report',
		args: {
			selected_items: selected_items,
		},
		freeze: true,
		freeze_message: __('Creating Purchase Orders...'),
		callback: (r) => {
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
						.map((po) => {
							return `<a href="/app/purchase-order/${po}" target="_blank">${po}</a>`;
						})
						.join(', ');

					frappe.msgprint({
						title: __('Purchase Orders Created'),
						message: __('Successfully created: {0}', [po_links]),
						indicator: 'green',
					});
				}

				report.refresh();
			}
		},
		error: (r) => {
			frappe.msgprint({
				title: __('Error'),
				message: __(
					'Failed to create Purchase Orders. Please check the error log.',
				),
				indicator: 'red',
			});
		},
	});
}
