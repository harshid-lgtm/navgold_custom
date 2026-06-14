// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Amazon Pricing Snapshot', {
	refresh(frm) {
		if (frm.is_new()) {
			frm.dashboard.set_headline(
				__(
					'Save this snapshot, then click <b>Capture Current Prices</b> to fill it from live pricing.',
				),
			);
			return;
		}

		if (!cint(frm.doc.item_count)) {
			frm
				.add_custom_button(__('Capture Current Prices'), async () => {
					const confirmed = await navgold.confirm_dialog(
						__(
							'Capture the current ERP min/max/strategy for every active SKU into this snapshot?',
						),
					);
					if (!confirmed) return;
					const { message } = await frm.call('capture_now');
					frappe.show_alert({
						message: __('Captured {0} SKUs', [message.item_count]),
						indicator: 'green',
					});
					frm.reload_doc();
				})
				.addClass('btn-primary');
			render_preview(frm);
			return;
		}

		frm.add_custom_button(__('Revert ERP Prices'), async () => {
			const confirmed = await navgold.confirm_dialog(
				__(
					'Overwrite current ERP min/max/strategy for {0} SKUs with this snapshot? This does not push to Amazon/Repricer yet.',
					[frm.doc.item_count],
				),
			);
			if (!confirmed) return;
			const { message } = await frm.call('revert_prices');
			frappe.show_alert({
				message: __('Reverted {0} SKUs ({1} skipped)', [
					message.reverted,
					message.skipped,
				]),
				indicator: 'orange',
			});
			frm.reload_doc();
		});

		frm.add_custom_button(
			__('Repricer'),
			async () => publish(frm, 'Repricer'),
			__('Publish'),
		);
		frm.add_custom_button(
			__('Amazon'),
			async () => publish(frm, 'Amazon'),
			__('Publish'),
		);

		render_preview(frm);
	},
});

function get_selected_pricing(frm) {
	const wrapper = frm.get_field('preview_html').$wrapper;
	return wrapper
		.find('input.snapshot-row-check:checked')
		.map((index, element) => element.dataset.pricing)
		.get();
}

async function publish(frm, channel) {
	const pricing_names = get_selected_pricing(frm);
	if (!pricing_names.length) {
		frappe.msgprint(__('Tick at least one SKU in the preview to publish.'));
		return;
	}
	const confirmed = await navgold.confirm_dialog(
		__(
			'Publish {0} selected SKU(s) from this snapshot to {1}? Guards (zero-cost, deviation, freeze) still apply.',
			[pricing_names.length, channel],
		),
	);
	if (!confirmed) return;
	const { message } = await frm.call('publish', { channel, pricing_names });
	const count =
		message.synced ?? message.message_count ?? message.submitted ?? '';
	frappe.show_alert({
		message: __('Published to {0}: {1}', [channel, count]),
		indicator: 'green',
	});
}

function render_preview(frm) {
	const wrapper = frm.get_field('preview_html').$wrapper;
	let rows = [];
	try {
		rows = JSON.parse(frm.doc.snapshot_data || '[]');
	} catch (e) {
		rows = [];
	}
	const sample = rows.slice(0, 500);
	const body = sample
		.map(
			(row) => `<tr>
				<td class="text-center"><input type="checkbox" class="snapshot-row-check" data-pricing="${frappe.utils.escape_html(
					row.pricing || '',
				)}" checked></td>
				<td>${frappe.utils.escape_html(row.proxy_sku || '')}</td>
				<td>${frappe.utils.escape_html(row.active_strategy || '')}</td>
				<td class="text-right">${format_currency(row.min_price || 0, 'GBP')}</td>
				<td class="text-right">${format_currency(row.max_price || 0, 'GBP')}</td>
				<td class="text-center">${row.fixed_price ? '✓' : ''}</td>
			</tr>`,
		)
		.join('');

	wrapper.html(`
		<div class="text-muted small" style="margin-bottom:6px">
			Showing ${sample.length} of ${rows.length} SKUs — only ticked rows are published
		</div>
		<div style="max-height:420px;overflow:auto">
			<table class="table table-bordered" style="font-size:12.5px">
				<thead><tr>
					<th class="text-center"><input type="checkbox" class="snapshot-check-all" checked></th>
					<th>SKU</th><th>Strategy</th><th class="text-right">Min</th>
					<th class="text-right">Max</th><th class="text-center">Fixed</th>
				</tr></thead>
				<tbody>${
					body ||
					'<tr><td colspan="6" class="text-center text-muted">Empty snapshot</td></tr>'
				}</tbody>
			</table>
		</div>
	`);

	wrapper.find('.snapshot-check-all').on('change', (event) => {
		wrapper
			.find('input.snapshot-row-check')
			.prop('checked', event.target.checked);
	});
}
