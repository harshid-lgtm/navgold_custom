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
				__('Manual Price Push'),
				() => manual_price_push(frm),
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

function manual_price_push(frm) {
	const fixed_value =
		flt(frm.doc.fixed_price_value) || flt(frm.doc.current_min_price);
	const dialog = new frappe.ui.Dialog({
		title: __('Manual Price Push to Amazon'),
		fields: [
			{
				fieldname: 'sku',
				fieldtype: 'Link',
				options: 'Proxy SKU',
				label: __('SKU'),
				default: frm.doc.proxy_sku,
				reqd: 1,
			},
			{
				fieldname: 'price_source',
				fieldtype: 'Select',
				label: __('Price Source'),
				options: ['Fixed Price', 'Current Min/Max'].join('\n'),
				default: 'Fixed Price',
				description: __('Pre-fills the prices below; you can still edit them.'),
			},
			{ fieldtype: 'Column Break' },
			{
				fieldname: 'min',
				fieldtype: 'Currency',
				label: __('Minimum'),
				default: flt(frm.doc.current_min_price),
				reqd: 1,
			},
			{
				fieldname: 'max',
				fieldtype: 'Currency',
				label: __('Maximum'),
				default: flt(frm.doc.current_max_price),
				reqd: 1,
			},
			{
				fieldname: 'our_price',
				fieldtype: 'Currency',
				label: __('Our Price'),
				default: fixed_value,
				reqd: 1,
			},
		],
		primary_action_label: __('Preview & Push'),
		async primary_action(values) {
			const update = {
				sku: values.sku,
				min: flt(values.min),
				max: flt(values.max),
				our_price: flt(values.our_price),
			};
			// Dry run first: this validates prices and throws on a bad min/max before we ever
			// offer the live push. The returned preview is intentionally not used here.
			await frappe.call({
				method:
					'navgold_custom.navgold_custom.amazon_pricing.manual_price_push.push_manual_prices',
				args: { updates: [update], live: false },
				freeze: true,
				freeze_message: __('Validating...'),
			});
			const confirmed = await navgold.confirm_dialog(
				__(
					'Push to Amazon for {0}? Min {1}, Max {2}, Our Price {3}. Guards still apply and this cannot be undone.',
					[
						update.sku,
						format_currency(update.min, 'GBP'),
						format_currency(update.max, 'GBP'),
						format_currency(update.our_price, 'GBP'),
					],
				),
			);
			if (!confirmed) return;
			const { message } = await frappe.call({
				method:
					'navgold_custom.navgold_custom.amazon_pricing.manual_price_push.push_manual_prices',
				args: { updates: [update], live: true },
				freeze: true,
				freeze_message: __('Submitting feed to Amazon...'),
			});
			dialog.hide();
			frappe.show_alert({
				message: __('Submitted feed {0} (log {1})', [
					message.feed_id,
					message.log_name,
				]),
				indicator: 'green',
			});
		},
	});

	dialog.fields_dict.price_source.$input.on('change', () => {
		const source = dialog.get_value('price_source');
		if (source === 'Fixed Price') {
			dialog.set_value('min', fixed_value);
			dialog.set_value('max', fixed_value);
			dialog.set_value('our_price', fixed_value);
		} else {
			dialog.set_value('min', flt(frm.doc.current_min_price));
			dialog.set_value('max', flt(frm.doc.current_max_price));
			dialog.set_value('our_price', flt(frm.doc.current_min_price));
		}
	});

	dialog.show();
}
