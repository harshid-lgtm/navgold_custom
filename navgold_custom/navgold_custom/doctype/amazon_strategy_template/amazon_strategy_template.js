// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Amazon Strategy Template', {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(
				__('Apply to Pricing'),
				() => {
					frappe.prompt(
						{
							fieldname: 'pricing',
							fieldtype: 'Link',
							options: 'Amazon Product Pricing',
							label: __('Amazon Product Pricing'),
							reqd: 1,
							get_query: () => {
								return {
									filters: {
										is_active: 1,
										fba_strategy: ['is', 'not set'],
									},
								};
							},
						},
						async (values) => {
							const response = await frappe.call({
								method:
									'navgold_custom.navgold_custom.amazon_pricing.template_applicator.apply_template_to_pricing',
								args: {
									template_name: frm.doc.name,
									pricing_name: values.pricing,
								},
								freeze: true,
								freeze_message: __('Creating strategies from template...'),
							});

							if (response.message) {
								frappe.msgprint({
									title: __('Template Applied'),
									message: __('Created {0} strategies for {1}', [
										response.message.strategies_created,
										values.pricing,
									]),
									indicator: 'green',
								});
							}
						},
						__('Apply Template to Pricing'),
						__('Apply'),
					);
				},
				__('Actions'),
			);

			frm.add_custom_button(
				__('Apply to All (No Strategies)'),
				async () => {
					const confirmed = await navgold.confirm_dialog(
						__(
							"This will apply this template to ALL active pricing records that don't have any strategies. Continue?",
						),
					);
					if (!confirmed) return;

					const response = await frappe.call({
						method:
							'navgold_custom.navgold_custom.amazon_pricing.template_applicator.bulk_apply_template',
						args: {
							template_name: frm.doc.name,
						},
						freeze: true,
						freeze_message: __('Applying template to all pricing records...'),
					});

					if (response.message) {
						frappe.msgprint({
							title: __('Bulk Application Complete'),
							message: __('Success: {0}, Errors: {1}', [
								response.message.success_count,
								response.message.error_count,
							]),
							indicator: response.message.error_count > 0 ? 'orange' : 'green',
						});
					}
				},
				__('Actions'),
			);
		}
	},

	validate(frm) {
		const types = {};
		for (const row of frm.doc.strategies) {
			if (types[row.strategy_type]) {
				frappe.throw(
					__("Strategy type '{0}' is duplicated", [row.strategy_type]),
				);
			}
			types[row.strategy_type] = true;
		}
	},
});
