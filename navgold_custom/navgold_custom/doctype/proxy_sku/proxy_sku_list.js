// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.listview_settings['Proxy SKU'] = {
	onload: (listview) => {
		listview.page.add_action_item(__('Apply Pricing Template'), async () => {
			const selected = listview.get_checked_items();
			if (selected.length === 0) {
				frappe.msgprint(__('Please select at least one record'));
				return;
			}

			const proxy_sku_names = selected.map((doc) => doc.name);

			const default_template = await frappe.db.get_value(
				'Amazon Strategy Template',
				{ is_default: 1 },
				'name',
			);

			const values = await new Promise((resolve) => {
				frappe.prompt(
					[
						{
							fieldname: 'template_name',
							label: __('Amazon Strategy Template'),
							fieldtype: 'Link',
							options: 'Amazon Strategy Template',
							reqd: 1,
							default: default_template.message?.name,
						},
					],
					(data) => resolve(data),
					__('Select Strategy Template'),
					__('Continue'),
				);
			});

			if (!values?.template_name) return;

			const confirmed = await navgold.confirm_dialog(
				__('Apply template to {0} proxy SKU(s)?', [proxy_sku_names.length]),
			);
			if (!confirmed) return;

			const response = await frappe.call({
				method:
					'navgold_custom.navgold_custom.amazon_pricing.template_applicator.bulk_apply_template_to_proxy_skus',
				args: {
					template_name: values.template_name,
					proxy_sku_names: JSON.stringify(proxy_sku_names),
				},
				freeze: true,
				freeze_message: __('Queuing template application...'),
			});

			frappe.msgprint(
				__('Queued template application for {0} proxy SKU(s).', [
					response.message.count,
				]),
			);
			listview.refresh();
		});

		frappe.realtime.on('bulk_template_proxy_sku_complete', (data) => {
			frappe.show_alert(
				{
					message: __(
						'Template applied: {0} created, {1} skipped, {2} errors',
						[data.applied, data.skipped, data.errors],
					),
					indicator: data.errors ? 'orange' : 'green',
				},
				7,
			);
			listview.refresh();
		});
	},
};
