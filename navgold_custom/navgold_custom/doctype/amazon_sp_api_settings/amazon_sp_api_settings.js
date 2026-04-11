// Attribution: Code adapted from eFeone's eSeller Suite (https://github.com/efeone/eseller_suite)
// Original work licensed under the MIT License
// Copyright (c) 2024, eFeone and contributors

// Modifications and additions
// Copyright (c) 2025, BWH Studios and contributors
// For license information, please see license.txt

frappe.ui.form.on('Amazon SP API Settings', {
	refresh(frm) {
		frm.trigger('set_queries');
		handle_retry_btn(frm);
	},
	set_queries(frm) {
		frm.set_query('warehouse', () => {
			return {
				filters: {
					is_group: 0,
					company: frm.doc.company,
				},
			};
		});

		frm.set_query('customer_group', () => {
			return {
				filters: {
					is_group: 0,
				},
			};
		});

		frm.set_query('market_place_account_group', () => {
			return {
				filters: {
					is_group: 1,
					company: frm.doc.company,
				},
			};
		});
	},
});

function handle_retry_btn(frm) {
	frm.add_custom_button(__('Get Order'), () => {
		const d = new frappe.ui.Dialog({
			title: __('Sync by Order ID'),
			fields: [
				{
					label: __('Amazon SP API Settings'),
					fieldname: 'sp_api_settings',
					fieldtype: 'Link',
					options: 'Amazon SP API Settings',
					reqd: 1,
					default: frm.doc.name,
					hidden: 1,
				},
				{
					label: __('Amazon Order ID'),
					fieldname: 'amazon_order_id',
					fieldtype: 'Data',
					reqd: 1,
				},
			],
			primary_action_label: 'Sync',
			primary_action(values) {
				d.hide();
				frappe.call({
					method:
						'navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_repository.get_order',
					args: {
						amz_setting_name: values.sp_api_settings,
						amazon_order_ids: values.amazon_order_id,
					},
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
						}
					},
				});
			},
		});
		d.show();
	});

	frm.add_custom_button(__('Get Orders'), () => {
		const d = new frappe.ui.Dialog({
			title: 'Sync by Order ID',
			fields: [
				{
					label: __('Amazon SP API Settings'),
					fieldname: 'sp_api_settings',
					fieldtype: 'Link',
					options: 'Amazon SP API Settings',
					reqd: 1,
					default: frm.doc.name,
					hidden: 1,
				},
				{
					label: __('Amazon Order IDs'),
					fieldname: 'amazon_order_ids',
					fieldtype: 'Small Text',
					reqd: 1,
				},
			],
			primary_action_label: 'Sync',
			primary_action(values) {
				d.hide();
				const amazon_order_ids = values.amazon_order_ids.split('\n');
				for (let i = 0; i < amazon_order_ids.length; i++) {
					frappe.call({
						method:
							'navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_repository.get_order',
						args: {
							amz_setting_name: values.sp_api_settings,
							amazon_order_ids: amazon_order_ids[i],
						},
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
							}
						},
					});
				}
			},
		});
		d.show();
	});
}
