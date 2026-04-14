// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Amazon Product Strategy', {
	refresh(frm) {
		if (!frm.is_new()) {
			// Add Apply Cost Template button
			frm.add_custom_button(
				__('Apply Cost Template'),
				() => {
					frappe.prompt(
						{
							fieldname: 'cost_template',
							fieldtype: 'Link',
							options: 'Amazon Cost Set Template',
							label: __('Cost Set Template'),
							reqd: 1,
						},
						(values) => {
							frappe.call({
								method: 'frappe.client.get',
								args: {
									doctype: 'Amazon Cost Set Template',
									name: values.cost_template,
								},
								callback: (r) => {
									if (r.message) {
										// Clear existing costs
										frm.clear_table('costs');

										// Add costs from template
										for (const cost of r.message.costs) {
											const row = frm.add_child('costs');
											row.cost_type = cost.cost_type;
											row.cost_sub_type = cost.cost_sub_type;
											row.value = cost.default_value;
											row.is_active = cost.is_active;
											row.rank = cost.rank;
											row.source = cost.source;
										}

										frm.refresh_field('costs');
										frappe.msgprint(
											__('Cost template applied. Please save to confirm.'),
										);
									}
								},
							});
						},
						__('Select Cost Template'),
						__('Apply'),
					);
				},
				__('Actions'),
			);

			// Add link to view pricing record for this proxy SKU
			if (frm.doc.proxy_sku) {
				frm.add_custom_button(
					__('View Pricing'),
					() => {
						frappe.db
							.get_value(
								'Amazon Product Pricing',
								{ proxy_sku: frm.doc.proxy_sku },
								'name',
							)
							.then((r) => {
								if (r.message?.name) {
									frappe.set_route(
										'Form',
										'Amazon Product Pricing',
										r.message.name,
									);
								} else {
									frappe.msgprint(
										__('No pricing record found for this Proxy SKU'),
									);
								}
							});
					},
					__('Actions'),
				);
			}

			// Add Recalculate Prices button
			frm.add_custom_button(
				__('Recalculate Prices'),
				() => {
					frappe.call({
						method:
							'navgold_custom.navgold_custom.amazon_pricing.price_calculator_api.calculate_prices_for_strategy',
						args: {
							strategy_name: frm.doc.name,
						},
						callback: (r) => {
							if (r.message) {
								frm.set_value('calculated_min_price', r.message.min_price);
								frm.set_value('calculated_max_price', r.message.max_price);
								frappe.msgprint(
									__('Prices recalculated. Save to confirm changes.'),
								);
							}
						},
					});
				},
				__('Actions'),
			);
		}

		// Set query filter for cost_sub_type based on cost_type
		frm.fields_dict.costs.grid.get_field('cost_sub_type').get_query = (
			doc,
			cdt,
			cdn,
		) => {
			const row = locals[cdt][cdn];
			return {
				filters: {
					cost_type: row.cost_type,
				},
			};
		};
	},

	validate(frm) {
		if (frm.doc.min_margin > frm.doc.max_margin) {
			frappe.throw(__('Min Margin cannot be greater than Max Margin'));
		}
	},

	target_price(frm) {
		// Calculate margin when target price changes
		if (frm.doc.target_price && frm.doc.target_price > 0) {
			frappe.call({
				method:
					'navgold_custom.navgold_custom.amazon_pricing.price_calculator_api.calculate_margin_for_strategy',
				args: {
					strategy_name: frm.doc.name,
					target_price: frm.doc.target_price,
				},
				callback: (r) => {
					if (r.message) {
						frm.set_value('calculated_margin', r.message.margin);
						frm.set_value('calculated_profit', r.message.profit);
					}
				},
			});
		} else {
			frm.set_value('calculated_margin', 0);
			frm.set_value('calculated_profit', 0);
		}
	},
});
