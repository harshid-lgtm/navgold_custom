// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Amazon Product Strategy', {
	refresh(frm) {
		refresh_value_editability(frm);
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
									row.value_mode = 'Auto';
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
			const filters = { is_group: 0 };
			if (row.cost_type) {
				filters.parent_cost_type = row.cost_type;
			}
			return { filters };
		};

		render_cost_filter_controls(frm);
	},

	validate(frm) {
		if (frm.doc.min_margin > frm.doc.max_margin) {
			frappe.throw(__('Min Margin cannot be greater than Max Margin'));
		}
	},

	costs_add(frm) {
		apply_cost_filters(frm);
		refresh_value_editability(frm);
	},

	costs_remove(frm) {
		apply_cost_filters(frm);
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

frappe.ui.form.on('Amazon Product Cost', {
	value_mode(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		// Switching to Auto hands the value to the feed/default; drop the stale hand-set number.
		if (row.value_mode === 'Auto' && row.value) {
			frappe.model.set_value(cdt, cdn, 'value', 0);
		}
		refresh_value_editability(frm);
	},
	async cost_sub_type(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.cost_sub_type) {
			return;
		}
		// The sub type is a leaf Cost Type; its parent group is the row's cost type.
		const response = await frappe.db.get_value(
			'Cost Type',
			row.cost_sub_type,
			'parent_cost_type',
		);
		const parent_cost_type = response.message?.parent_cost_type;
		if (parent_cost_type) {
			frappe.model.set_value(cdt, cdn, 'cost_type', parent_cost_type);
		}
	},
});

function refresh_value_editability(frm) {
	// Value is editable only for Manual rows; Auto rows are owned by a feed or default. Set this
	// per row here because read_only_depends_on evaluates unreliably per row in the inline grid.
	const grid = frm.fields_dict.costs?.grid;
	if (!grid?.grid_rows) {
		return;
	}
	for (const grid_row of grid.grid_rows) {
		const value_field = (grid_row.docfields || []).find(
			(field) => field.fieldname === 'value',
		);
		if (value_field) {
			value_field.read_only = grid_row.doc.value_mode === 'Auto' ? 1 : 0;
		}
	}
	grid.refresh();
}

function render_cost_filter_controls(frm) {
	const grid_field = frm.fields_dict.costs;
	if (!grid_field) {
		return;
	}

	// Filter state is kept on frm, which is reused across documents of the same
	// doctype — reset it when the user navigates to another strategy.
	if (frm.cost_filter_docname !== frm.doc.name) {
		frm.cost_filter_docname = frm.doc.name;
		frm.cost_filter_state = { cost_type: null, cost_sub_type: null };
		if (frm.cost_type_filter_control) {
			frm.cost_type_filter_control.set_value('');
			frm.cost_sub_type_filter_control.set_value('');
		}
	}

	if (!grid_field.$wrapper.find('.cost-filter-controls').length) {
		const filter_wrapper = $(`
			<div class="cost-filter-controls row">
				<div class="col-sm-3 cost-type-filter"></div>
				<div class="col-sm-3 cost-sub-type-filter"></div>
				<div class="col-sm-6 cost-filter-meta">
					<button class="btn btn-default btn-xs cost-filter-reset hidden">
						${__('Reset Filters')}
					</button>
					<span class="text-muted small cost-filter-count"></span>
				</div>
			</div>
		`).prependTo(grid_field.$wrapper);

		frm.cost_type_filter_control = frappe.ui.form.make_control({
			parent: filter_wrapper.find('.cost-type-filter'),
			df: {
				fieldtype: 'Link',
				fieldname: 'cost_type_filter',
				label: __('Filter by Cost Type'),
				options: 'Cost Type',
				get_query: () => ({ filters: { is_group: 1 } }),
				change: () => {
					frm.cost_filter_state.cost_type = cstr(
						frm.cost_type_filter_control.get_value(),
					);
					// A sub type from another cost type can't match anymore
					frm.cost_filter_state.cost_sub_type = null;
					frm.cost_sub_type_filter_control.set_value('');
					apply_cost_filters(frm);
				},
			},
			render_input: true,
		});

		frm.cost_sub_type_filter_control = frappe.ui.form.make_control({
			parent: filter_wrapper.find('.cost-sub-type-filter'),
			df: {
				fieldtype: 'Link',
				fieldname: 'cost_sub_type_filter',
				label: __('Filter by Sub Type'),
				options: 'Cost Type',
				get_query: () => {
					const filters = { is_group: 0 };
					if (frm.cost_filter_state.cost_type) {
						filters.parent_cost_type = frm.cost_filter_state.cost_type;
					}
					return { filters };
				},
				change: () => {
					frm.cost_filter_state.cost_sub_type = cstr(
						frm.cost_sub_type_filter_control.get_value(),
					);
					apply_cost_filters(frm);
				},
			},
			render_input: true,
		});

		filter_wrapper.find('.cost-filter-reset').on('click', () => {
			frm.cost_filter_state = { cost_type: null, cost_sub_type: null };
			frm.cost_type_filter_control.set_value('');
			frm.cost_sub_type_filter_control.set_value('');
			apply_cost_filters(frm);
		});
	}

	apply_cost_filters(frm);
}

function apply_cost_filters(frm) {
	const grid_field = frm.fields_dict.costs;
	if (!grid_field || !frm.cost_filter_state) {
		return;
	}

	const { cost_type, cost_sub_type } = frm.cost_filter_state;
	const filter_active = Boolean(cost_type || cost_sub_type);
	const grid_rows = grid_field.grid.grid_rows || [];

	let visible_count = 0;
	for (const grid_row of grid_rows) {
		// Unsaved rows stay visible so a freshly added row doesn't vanish mid-edit
		const matches =
			!filter_active ||
			Boolean(grid_row.doc.__islocal) ||
			((!cost_type || grid_row.doc.cost_type === cost_type) &&
				(!cost_sub_type || grid_row.doc.cost_sub_type === cost_sub_type));
		grid_row.wrapper.toggle(matches);
		if (matches) {
			visible_count += 1;
		}
	}

	grid_field.$wrapper
		.find('.cost-filter-count')
		.text(
			filter_active
				? __('Showing {0} of {1} costs', [visible_count, grid_rows.length])
				: '',
		);
	grid_field.$wrapper
		.find('.cost-filter-reset')
		.toggleClass('hidden', !filter_active);
}
