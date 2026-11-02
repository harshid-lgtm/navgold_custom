// Copyright (c) 2026, Navgold and contributors
// For license information, please see license.txt

frappe.query_reports['Pending Price Approvals'] = {
	onload(report) {
		report.page.add_inner_button(__('Approve Selected'), async () => {
			await approve_selected(report);
		});

		report.page.add_inner_button(__('Approve All Pending'), async () => {
			await approve_all(report);
		});
	},

	get_datatable_options(options) {
		return Object.assign(options, {
			checkboxColumn: true,
		});
	},

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);

		if (!data) return formatted;

		if (['min_change', 'max_change', 'max_change_percent'].includes(column.fieldname)) {
			const numeric = flt(value);
			if (numeric > 0) {
				return `<span style="color:#b54708;font-weight:600">${formatted}</span>`;
			}
			if (numeric < 0) {
				return `<span style="color:#b42318;font-weight:600">${formatted}</span>`;
			}
		}

		if (column.fieldname === 'is_current_active' && cint(value)) {
			return `<span class="indicator-pill green">${__('Active')}</span>`;
		}

		return formatted;
	},
};

async function approve_selected(report) {
	const selected = report.get_checked_items();
	if (!selected.length) {
		frappe.msgprint(__('Please select at least one pending strategy.'));
		return;
	}

	const confirmed = await navgold.confirm_dialog(
		__(
			'Approve the proposed price band for {0} selected strategy row(s)? The approved prices will become eligible for the next Amazon/Repricer sync.',
			[selected.length],
		),
	);
	if (!confirmed) return;

	await run_approval(report, selected.map((row) => row.strategy), false);
}

async function approve_all(report) {
	const row_count = report.data?.length || 0;
	if (!row_count) {
		frappe.msgprint(__('There are no pending price approvals.'));
		return;
	}

	const confirmed = await navgold.confirm_dialog(
		__(
			'Approve ALL {0} pending strategy price changes? This includes active and inactive strategies.',
			[row_count],
		),
	);
	if (!confirmed) return;

	await run_approval(report, [], true);
}

async function run_approval(report, strategy_names, approve_all_pending) {
	const response = await frappe.call({
		method:
			'navgold_custom.navgold_custom.report.pending_price_approvals.pending_price_approvals.approve_price_changes',
		args: {
			strategy_names,
			approve_all: approve_all_pending ? 1 : 0,
		},
		freeze: true,
		freeze_message: __('Approving price changes...'),
	});

	const result = response.message || {};
	const skipped = result.skipped || [];

	frappe.show_alert(
		{
			message: __('{0} price change(s) approved.', [result.approved || 0]),
			indicator: skipped.length ? 'orange' : 'green',
		},
		5,
	);

	if (skipped.length) {
		const rows_html = skipped
			.map(
				(row) =>
					`<tr><td>${frappe.utils.escape_html(row.strategy)}</td><td>${frappe.utils.escape_html(
						row.reason,
					)}</td></tr>`,
			)
			.join('');

		frappe.msgprint({
			title: __('Skipped Price Approvals'),
			message: `
				<table class="table table-bordered">
					<thead><tr><th>${__('Strategy')}</th><th>${__('Reason')}</th></tr></thead>
					<tbody>${rows_html}</tbody>
				</table>
			`,
			indicator: 'orange',
		});
	}

	report.refresh();
}
