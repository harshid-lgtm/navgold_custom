// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.query_reports['Amazon Purchase Cost Overview'] = {
	onload(report) {
		add_edit_btn_styles();
		add_edit_event_listener(report);

		report.page.add_inner_button(__('Bulk Update Source'), async () => {
			await open_bulk_update_dialog(report);
		});

		report.page.add_inner_button(__('Save Overrides'), async () => {
			await save_staged_changes(report);
		});
	},

	get_datatable_options(options) {
		return Object.assign(options, {
			checkboxColumn: true,
		});
	},

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);

		if (column.fieldname === 'current_mode' && data) {
			const staged = staged_pricing_changes.get_staged(data.strategy);
			return format_cost_mode_cell(data.strategy, value, staged);
		}

		if (column.fieldname === 'current_value' && data) {
			const staged = staged_pricing_changes.get_staged(data.strategy);
			return format_cost_value_cell(
				data.strategy,
				cint(data.is_fba),
				data.current_mode,
				flt(data.current_value),
				staged,
			);
		}

		if (
			[
				'supplier_sync_preview',
				'po_sync_preview',
				'fba_transfer_preview',
			].includes(column.fieldname) &&
			data &&
			!is_null(value)
		) {
			const current = flt(data.current_value);
			const preview = flt(value);
			if (current && Math.abs(current - preview) > 0.0001) {
				return `<span style="color:#b54708">${formatted}</span>`;
			}
		}

		return formatted;
	},
};

const staged_pricing_changes = {
	staged: {},

	get_staged(strategy_name) {
		return this.staged[strategy_name] || null;
	},

	set_staged(strategy_name, value_mode, value) {
		this.staged[strategy_name] = { value_mode, value };
	},

	clear_staged(strategy_name) {
		delete this.staged[strategy_name];
	},

	get_all_staged() {
		return Object.entries(this.staged).map(([strategy, edit]) => ({
			strategy,
			value_mode: edit.value_mode,
			value: edit.value,
		}));
	},

	count() {
		return Object.keys(this.staged).length;
	},

	reset() {
		this.staged = {};
	},
};

function format_cost_value_cell(
	strategy,
	is_fba,
	current_mode,
	current_value,
	staged,
) {
	const edit_icon = `<span class="edit-btn" data-strategy="${strategy}" data-is-fba="${is_fba}" data-current-mode="${
		current_mode || ''
	}" data-current-value="${current_value}" title="${__(
		'Edit override',
	)}">${frappe.utils.icon('edit', 'sm')}</span>`;

	let value_html;
	if (staged) {
		const display_value =
			staged.value_mode === 'Manual'
				? format_currency(staged.value)
				: __('(auto)');
		value_html = `<span style="color:#b54708;font-weight:600">${display_value}</span>`;
	} else {
		value_html = `<div style="text-align: right">${format_currency(
			current_value,
		)}</div>`;
	}

	return `<div class="edit-btn-wrapper">${value_html}${edit_icon}</div>`;
}

function format_cost_mode_cell(strategy, current_mode, staged) {
	let inner = '';
	if (staged) {
		inner = `<span style="color:#b54708;font-weight:600">${staged.value_mode} <small>(staged)</small></span>`;
	} else if (current_mode) {
		const color = current_mode === 'Manual' ? '#b54708' : '#067647';
		inner = `<span style="color:${color};font-weight:500">${current_mode}</span>`;
	}
	return `<span class="mode-cell-content" data-strategy="${strategy}">${inner}</span>`;
}

function refresh_strategy_row(strategy) {
	const staged = staged_pricing_changes.get_staged(strategy);
	const pencil = document.querySelector(
		`.edit-btn[data-strategy="${CSS.escape(strategy)}"]`,
	);
	if (!pencil) return;

	const is_fba = cint(pencil.getAttribute('data-is-fba'));
	const current_mode = pencil.getAttribute('data-current-mode');
	const current_value = flt(pencil.getAttribute('data-current-value'));

	const wrapper = pencil.closest('.edit-btn-wrapper');
	if (wrapper) {
		wrapper.outerHTML = format_cost_value_cell(
			strategy,
			is_fba,
			current_mode,
			current_value,
			staged,
		);
	}

	const mode_span = document.querySelector(
		`.mode-cell-content[data-strategy="${CSS.escape(strategy)}"]`,
	);
	if (mode_span) {
		mode_span.outerHTML = format_cost_mode_cell(strategy, current_mode, staged);
	}
}

function add_edit_btn_styles() {
	if (document.getElementById('add-edit-btn-styles')) return;

	const style = document.createElement('style');
	style.id = 'add-edit-btn-styles';
	style.innerHTML = `
		.edit-btn-wrapper {
			display: flex;
			align-items: center;
			justify-content: flex-end;
			gap: 0.5rem;
		}

		.edit-btn {
			color: var(--text-muted);
			cursor: pointer;
			transition: color 0.2s ease;
		}

		.edit-btn:hover {
			color: var(--text-color);
		}
	`;
	document.head.appendChild(style);
}

function add_edit_event_listener(report) {
	$(document)
		.off('click.purchase_cost_override')
		.on('click.purchase_cost_override', '.edit-btn', function () {
			const strategy = $(this).data('strategy');
			const is_fba = cint($(this).data('is-fba'));
			const current_mode = $(this).data('current-mode');
			const current_value = flt($(this).data('current-value'));
			open_per_row_edit_dialog(
				report,
				strategy,
				is_fba,
				current_mode,
				current_value,
			);
		});
}

function open_per_row_edit_dialog(
	report,
	strategy,
	is_fba,
	current_mode,
	current_value,
) {
	const existing_staged = staged_pricing_changes.get_staged(strategy);

	const dialog = new frappe.ui.Dialog({
		title: __('Edit Purchase Cost — {0}', [strategy]),
		fields: [
			{
				fieldtype: 'HTML',
				fieldname: 'current_info',
				options: `<p class="text-muted">${__(
					'Current',
				)}: <strong>${frappe.utils.escape_html(
					current_mode || '-',
				)}</strong> @ ${format_currency(current_value)}</p>`,
			},
			{
				fieldname: 'value_mode',
				fieldtype: 'Select',
				label: __('Mode'),
				reqd: 1,
				options: 'Auto\nManual',
				default: existing_staged?.value_mode || current_mode || 'Auto',
			},
			{
				fieldname: 'value',
				fieldtype: 'Currency',
				label: __('Value'),
				depends_on: "eval:doc.value_mode == 'Manual'",
				mandatory_depends_on: "eval:doc.value_mode == 'Manual'",
				default:
					existing_staged?.value_mode === 'Manual'
						? existing_staged.value
						: current_value,
			},
		],
		primary_action_label: existing_staged ? __('Update Stage') : __('Stage'),
		primary_action: (values) => {
			staged_pricing_changes.set_staged(
				strategy,
				values.value_mode,
				values.value_mode === 'Manual' ? flt(values.value) : null,
			);
			refresh_strategy_row(strategy);
			dialog.hide();
			frappe.show_alert(
				{
					message: __('Staged. {0} override(s) pending.', [
						staged_pricing_changes.count(),
					]),
					indicator: 'blue',
				},
				3,
			);
		},
	});

	if (existing_staged) {
		dialog.set_secondary_action_label(__('Remove Stage'));
		dialog.set_secondary_action(() => {
			staged_pricing_changes.clear_staged(strategy);
			refresh_strategy_row(strategy);
			dialog.hide();
		});
	}

	dialog.show();
}

async function save_staged_changes(report) {
	const staged = staged_pricing_changes.get_all_staged();
	if (staged.length === 0) {
		frappe.msgprint(
			__('No staged overrides. Click the edit icon on a row to stage.'),
		);
		return;
	}

	const confirmed = await navgold.confirm_dialog(
		__('Commit {0} staged override(s)?', [staged.length]),
	);
	if (!confirmed) return;

	await save_purchase_cost_updates(
		report,
		staged,
		__('Committing staged overrides...'),
	);
	staged_pricing_changes.reset();
}

async function save_purchase_cost_updates(report, payload, freeze_message) {
	const response = await frappe.call({
		method:
			'navgold_custom.navgold_custom.report.amazon_purchase_cost_overview.amazon_purchase_cost_overview.bulk_update_purchase_cost_source',
		args: { rows: payload },
		freeze: true,
		freeze_message,
	});

	const { updated, skipped } = response.message;

	frappe.show_alert(
		{
			message: __('{0} updated, {1} skipped', [updated, skipped.length]),
			indicator: skipped.length ? 'orange' : 'green',
		},
		5,
	);

	if (skipped.length) {
		show_skipped_dialog(skipped);
	}

	report.refresh();
}

async function open_bulk_update_dialog(report) {
	const selected_rows = report.get_checked_items();
	if (selected_rows.length === 0) {
		frappe.msgprint(__('Please select at least one row.'));
		return;
	}

	const dialog = new frappe.ui.Dialog({
		title: __('Bulk Update Purchase Cost Source'),
		fields: [
			{
				fieldname: 'selected_count',
				fieldtype: 'HTML',
				options: `<p>${__('Updating {0} selected strategy row(s).', [
					selected_rows.length,
				])}</p>`,
			},
			{
				fieldname: 'value_mode',
				fieldtype: 'Select',
				label: __('Mode'),
				reqd: 1,
				options: 'Auto\nManual',
			},
			{
				fieldname: 'value',
				fieldtype: 'Currency',
				label: __('Value'),
				depends_on: "eval:doc.value_mode == 'Manual'",
				mandatory_depends_on: "eval:doc.value_mode == 'Manual'",
			},
		],
		primary_action_label: __('Apply'),
		primary_action: async (values) => {
			const confirmed = await navgold.confirm_dialog(
				__("Update {0} row(s) to mode '{1}'?", [
					selected_rows.length,
					values.value_mode,
				]),
			);
			if (!confirmed) return;

			dialog.hide();
			await save_bulk_update(report, selected_rows, values);
		},
	});

	dialog.show();
}

async function save_bulk_update(report, selected_rows, values) {
	const payload = selected_rows.map((row) => ({
		strategy: row.strategy,
		value_mode: values.value_mode,
		value: values.value_mode === 'Manual' ? flt(values.value) : null,
	}));

	await save_purchase_cost_updates(
		report,
		payload,
		__('Updating purchase cost sources...'),
	);
}

function show_skipped_dialog(skipped) {
	const rows_html = skipped
		.map(
			(row) =>
				`<tr><td>${frappe.utils.escape_html(
					row.strategy,
				)}</td><td>${frappe.utils.escape_html(row.reason)}</td></tr>`,
		)
		.join('');

	frappe.msgprint({
		title: __('Skipped Rows'),
		message: `
			<table class="table table-bordered">
				<thead><tr><th>${__('Strategy')}</th><th>${__('Reason')}</th></tr></thead>
				<tbody>${rows_html}</tbody>
			</table>
		`,
		indicator: 'orange',
	});
}
