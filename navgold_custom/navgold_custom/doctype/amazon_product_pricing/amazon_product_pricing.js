// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Amazon Product Pricing', {
	refresh(frm) {
		if (!frm.is_new()) {
			// Add Submit to Amazon button
			frm.add_custom_button(
				__('Submit to Amazon'),
				() => {
					frappe.confirm(
						__('Are you sure you want to submit this pricing to Amazon?'),
						() => {
							frm.call({
								method: 'submit_to_amazon',
								doc: frm.doc,
								freeze: true,
								freeze_message: __('Submitting to Amazon...'),
								callback: (r) => {
									if (r.message) {
										frappe.msgprint(
											__('Pricing submitted to Amazon successfully'),
										);
										frm.reload_doc();
									}
								},
							});
						},
					);
				},
				__('Actions'),
			);

			// Add Sync to Repricer button
			frm.add_custom_button(
				__('Sync to Repricer'),
				() => {
					frm.call({
						method: 'sync_to_repricer',
						doc: frm.doc,
						freeze: true,
						freeze_message: __('Syncing to Repricer...'),
						callback: (r) => {
							if (r.message) {
								frappe.msgprint(__('Pricing synced to Repricer successfully'));
								frm.reload_doc();
							}
						},
					});
				},
				__('Actions'),
			);

			// Add Create Strategy button
			frm.add_custom_button(
				__('Create Strategy'),
				() => {
					frappe.new_doc('Amazon Product Strategy', {
						proxy_sku: frm.doc.proxy_sku,
					});
				},
				__('Actions'),
			);

			// Add Apply Template button
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
						(values) => {
							frappe.call({
								method:
									'navgold_custom.navgold_custom.amazon_pricing.template_applicator.apply_template_to_pricing',
								args: {
									template_name: values.template,
									pricing_name: frm.doc.name,
								},
								freeze: true,
								freeze_message: __('Creating strategies from template...'),
								callback: (r) => {
									if (r.message?.success) {
										frappe.msgprint({
											title: __('Template Applied'),
											message: __('Created {0} strategies successfully', [
												r.message.strategies_created,
											]),
											indicator: 'green',
										});
										frm.reload_doc();
									}
								},
							});
						},
						__('Apply Strategy Template'),
						__('Apply'),
					);
				},
				__('Actions'),
			);
		}

		// Set query filters for strategy fields - filter by proxy_sku
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
});
