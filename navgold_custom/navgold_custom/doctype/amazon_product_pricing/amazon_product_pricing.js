// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Amazon Product Pricing', {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(
				__('Submit to Amazon'),
				async () => {
					const confirmed = await navgold.confirm_dialog(
						__('Are you sure you want to submit this pricing to Amazon?'),
					);
					if (!confirmed) return;

					const response = await frm.call({
						method: 'submit_to_amazon',
						doc: frm.doc,
						freeze: true,
						freeze_message: __('Submitting to Amazon...'),
					});
					if (response.message) {
						frappe.msgprint(__('Pricing submitted to Amazon successfully'));
						frm.reload_doc();
					}
				},
				__('Actions'),
			);

			frm.add_custom_button(
				__('Sync to Repricer'),
				async () => {
					const response = await frm.call({
						method: 'sync_to_repricer',
						doc: frm.doc,
						freeze: true,
						freeze_message: __('Syncing to Repricer...'),
					});
					if (response.message) {
						frappe.msgprint(__('Pricing synced to Repricer successfully'));
						frm.reload_doc();
					}
				},
				__('Actions'),
			);

			frm.add_custom_button(
				__('Create Strategy'),
				() => {
					frappe.new_doc('Amazon Product Strategy', {
						proxy_sku: frm.doc.proxy_sku,
					});
				},
				__('Actions'),
			);

			frm.add_custom_button(
				__('Apply Strategy Template'),
				() => {
					frappe.prompt(
						{
							fieldname: 'template',
							fieldtype: 'Link',
							options: 'Amazon Strategy Template',
							label: __('Strategy Template'),
							reqd: 1,
							description: __(
								'This will create all 4 strategies (FBA, SFP, Backorder, Default) from the template',
							),
						},
						async (values) => {
							const response = await frappe.call({
								method:
									'navgold_custom.navgold_custom.amazon_pricing.template_applicator.apply_template_to_pricing',
								args: {
									template_name: values.template,
									pricing_name: frm.doc.name,
								},
								freeze: true,
								freeze_message: __('Creating strategies from template...'),
							});
							if (response.message?.success) {
								frappe.msgprint({
									title: __('Template Applied'),
									message: __('Created {0} strategies successfully', [
										response.message.strategies_created,
									]),
									indicator: 'green',
								});
								frm.reload_doc();
							}
						},
						__('Apply Strategy Template'),
						__('Apply'),
					);
				},
				__('Actions'),
			);
		}

		const strategy_fields = [
			'fba_strategy',
			'sfp_strategy',
			'backorder_strategy',
			'default_strategy',
		];
		for (const fieldname of strategy_fields) {
			frm.set_query(fieldname, () => ({
				filters: {
					proxy_sku: frm.doc.proxy_sku,
				},
			}));
		}
	},

	fixed_price(frm) {
		if (frm.doc.fixed_price) {
			frm.set_value('current_min_price', frm.doc.fixed_price_value || 0);
			frm.set_value('current_max_price', frm.doc.fixed_price_value || 0);
		}
	},

	fixed_price_value(frm) {
		if (frm.doc.fixed_price) {
			frm.set_value('current_min_price', frm.doc.fixed_price_value || 0);
			frm.set_value('current_max_price', frm.doc.fixed_price_value || 0);
		}
	},
});
