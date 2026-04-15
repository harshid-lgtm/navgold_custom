// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Amazon Product Strategy', {
	refresh(frm) {
		if (!frm.is_new()) {
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
						async (values) => {
							const response = await frappe.call({
								method: 'frappe.client.get',
								args: {
									doctype: 'Amazon Cost Set Template',
									name: values.cost_template,
								},
							});

							if (response.message) {
								frm.clear_table('costs');

								for (const cost of response.message.costs) {
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
						__('Select Cost Template'),
						__('Apply'),
					);
				},
				__('Actions'),
			);

			if (frm.doc.proxy_sku) {
				frm.add_custom_button(
					__('View Pricing'),
					async () => {
						const response = await frappe.db.get_value(
							'Amazon Product Pricing',
							{ proxy_sku: frm.doc.proxy_sku },
							'name',
						);

						if (response.message?.name) {
							frappe.set_route(
								'Form',
								'Amazon Product Pricing',
								response.message.name,
							);
						} else {
							frappe.msgprint(__('No pricing record found for this Proxy SKU'));
						}
					},
					__('Actions'),
				);
			}

			frm.add_custom_button(
				__('Recalculate Prices'),
				async () => {
					const response = await frappe.call({
						method:
							'navgold_custom.navgold_custom.amazon_pricing.price_calculator_api.calculate_prices_for_strategy',
						args: {
							strategy_name: frm.doc.name,
						},
					});

					if (response.message) {
						frm.set_value('calculated_min_price', response.message.min_price);
						frm.set_value('calculated_max_price', response.message.max_price);
						frappe.msgprint(
							__('Prices recalculated. Save to confirm changes.'),
						);
					}
				},
				__('Actions'),
			);
		}

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
		if (frm.doc.target_price && frm.doc.target_price > 0) {
			frappe
				.call({
					method:
						'navgold_custom.navgold_custom.amazon_pricing.price_calculator_api.calculate_margin_for_strategy',
					args: {
						strategy_name: frm.doc.name,
						target_price: frm.doc.target_price,
					},
				})
				.then((response) => {
					if (response.message) {
						frm.set_value('calculated_margin', response.message.margin);
						frm.set_value('calculated_profit', response.message.profit);
					}
				});
		} else {
			frm.set_value('calculated_margin', 0);
			frm.set_value('calculated_profit', 0);
		}
	},
});
