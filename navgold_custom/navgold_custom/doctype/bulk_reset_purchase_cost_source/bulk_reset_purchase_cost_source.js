// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Bulk Reset Purchase Cost Source', {
	refresh(frm) {
		frm.disable_save();
		frm.page.set_primary_action(__('Apply Reset'), async () => {
			await apply_reset(frm);
		});
		add_completion_listener();
	},
});

async function apply_reset(frm) {
	if (!frm.doc.cost_type) {
		frappe.msgprint(__('Cost Type is required.'));
		return;
	}
	if (!frm.doc.target_mode) {
		frappe.msgprint(__('Select a Target Mode.'));
		return;
	}
	if (frm.doc.target_mode === 'Manual' && is_null(frm.doc.manual_value)) {
		frappe.msgprint(__('Manual Value is required when Target Mode is Manual.'));
		return;
	}

	const confirmed = await navgold.confirm_dialog(
		__(
			"Enqueue background reset of '{0}' cost rows to mode '{1}'? Prices will recompute after.",
			[frm.doc.cost_type, frm.doc.target_mode],
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
		frappe.msgprint({
			title: __('Bulk Reset Complete'),
			indicator: 'green',
			message: __(
				"Reset {0} cost row(s) to mode '{1}' (matched {2}). {3} strategies enqueued for price recompute across {4} chunk(s).",
				[
					payload.updated,
					payload.target_mode,
					payload.matched,
					payload.enqueued_strategies,
					payload.chunks_enqueued,
				],
			),
		});
	});
}
