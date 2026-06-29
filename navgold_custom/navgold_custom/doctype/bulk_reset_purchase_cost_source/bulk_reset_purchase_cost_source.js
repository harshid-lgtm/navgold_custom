// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Bulk Reset Purchase Cost Source', {
	setup(frm) {
		frm.set_query('cost_sub_type', () => ({
			filters: { cost_type: frm.doc.cost_type },
		}));
	},
	refresh(frm) {
		frm.disable_save();
		frm.page.set_primary_action(__('Apply Reset'), async () => {
			await apply_reset(frm);
		});
		add_completion_listener();
	},
	cost_type(frm) {
		frm.set_value('cost_sub_type', null);
	},
});

async function apply_reset(frm) {
	if (!frm.doc.cost_type) {
		frappe.msgprint(__('Cost Type is required.'));
		return;
	}
	if (!frm.doc.target_source) {
		frappe.msgprint(__('Select a Target Source.'));
		return;
	}
	if (frm.doc.target_source === 'Manual' && is_null(frm.doc.manual_value)) {
		frappe.msgprint(
			__('Manual Value is required when Target Source is Manual.'),
		);
		return;
	}

	const confirmed = await navgold.confirm_dialog(
		__(
			"Enqueue background reset of '{0}' cost rows to source '{1}'? Prices will recompute after.",
			[frm.doc.cost_type, frm.doc.target_source],
		),
	);
	if (!confirmed) return;

	const response = await frm.call({
		method: 'apply_reset',
		doc: frm.doc,
		freeze: true,
		freeze_message: __('Enqueueing bulk reset...'),
	});

	const { matched_preview } = response.message;
	frappe.show_alert(
		{
			message: __(
				"Bulk reset enqueued for {0} cost row(s). You'll be notified when it completes.",
				[matched_preview],
			),
			indicator: 'blue',
		},
		7,
	);
}

function add_completion_listener() {
	frappe.realtime.off('bulk_reset_purchase_cost_complete');
	frappe.realtime.on('bulk_reset_purchase_cost_complete', (payload) => {
		const breakdown_rows = Object.entries(payload.target_breakdown || {})
			.map(
				([target, count]) =>
					`<tr><td>${frappe.utils.escape_html(
						target,
					)}</td><td class="text-right">${count}</td></tr>`,
			)
			.join('');

		const fba_note = payload.fba_fee_refresh_enqueued
			? `<p>${__(
					'An FBA fee refresh has been queued to repopulate the Amazon-Sync values.',
			  )}</p>`
			: '';

		frappe.msgprint({
			title: __('Bulk Reset Complete'),
			indicator: 'green',
			message: `
				<p>${__(
					'Matched {0}, updated {1}. {2} strategies enqueued for price recompute across {3} chunk(s).',
					[
						payload.matched,
						payload.updated,
						payload.enqueued_strategies,
						payload.chunks_enqueued,
					],
				)}</p>
				${fba_note}
				<table class="table table-bordered">
					<thead><tr><th>${__('Target Source')}</th><th class="text-right">${__(
						'Rows',
					)}</th></tr></thead>
					<tbody>${breakdown_rows}</tbody>
				</table>
			`,
		});
	});
}
