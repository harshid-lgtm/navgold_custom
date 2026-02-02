// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Amazon Strategy Template', {
	refresh(frm) {
		if (!frm.is_new()) {
			// Add button to apply template to a single pricing record
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
								// Only show pricing records without strategies
								return {
									filters: {
										is_active: 1,
										fba_strategy: ['is', 'not set'],
									},
								};
							},
						},
						(values) => {
							frappe.call({
								method:
									'navgold_custom.navgold_custom.amazon_pricing.template_applicator.apply_template_to_pricing',
								args: {
									template_name: frm.doc.name,
									pricing_name: values.pricing,
								},
								freeze: true,
								freeze_message: __('Creating strategies from template...'),
								callback: (r) => {
									if (r.message?.success) {
										frappe.msgprint({
											title: __('Template Applied'),
											message: __('Created {0} strategies for {1}', [
												r.message.strategies_created,
												values.pricing,
											]),
											indicator: 'green',
										});
									}
								},
							});
						},
						__('Apply Template to Pricing'),
						__('Apply'),
					);
				},
				__('Actions'),
			);

			// Add button to bulk apply to all pricing without strategies
			frm.add_custom_button(
				__('Apply to All (No Strategies)'),
				() => {
					frappe.confirm(
						__(
							"This will apply this template to ALL active pricing records that don't have any strategies. Continue?",
						),
						() => {
							frappe.call({
								method:
									'navgold_custom.navgold_custom.amazon_pricing.template_applicator.bulk_apply_template',
								args: {
									template_name: frm.doc.name,
								},
								freeze: true,
								freeze_message: __(
									'Applying template to all pricing records...',
								),
								callback: (r) => {
									if (r.message) {
										frappe.msgprint({
											title: __('Bulk Application Complete'),
											message: __('Success: {0}, Errors: {1}', [
												r.message.success_count,
												r.message.error_count,
											]),
											indicator: r.message.error_count > 0 ? 'orange' : 'green',
										});
									}
								},
							});
						},
					);
				},
				__('Actions'),
			);
		}
	},

	validate(frm) {
		// Ensure strategy types are unique
		const types = {};
		for (const row of frm.doc.strategies) {
			if (types[row.strategy_type]) {
				frappe.msgprint(
					__("Strategy type '{0}' is duplicated", [row.strategy_type]),
				);
				frappe.validated = false;
			}
			types[row.strategy_type] = true;
		}
	},
});
