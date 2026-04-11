// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Amazon Failed Sync Record', {
	refresh(frm) {
		frm.disable_save();
		frm.disable_form();
		if (!frm.is_new() && frm.doc.amazon_order_id) {
			frm
				.add_custom_button(__('Retry'), () => {
					retry_fetching(frm);
				})
				.addClass('btn-primary');
		}
	},
});

function retry_fetching(frm) {
	frm.call({
		method: 'retry_fetching',
		doc: frm.doc,
		freeze: true,
		freeze_message: __('Syncing Sales Order..'),
		callback: (r) => {
			if (r?.message) {
				frappe.show_alert(
					{
						message: __('Sales Orders created/updated successfully'),
						indicator: 'green',
					},
					5,
				);
			} else {
				frappe.show_alert(
					{
						message: __(
							'Failed to create/update Sales Order. Please check Amazon Failed Sync Record',
						),
						indicator: 'red',
					},
					5,
				);
			}
		},
	});
}
