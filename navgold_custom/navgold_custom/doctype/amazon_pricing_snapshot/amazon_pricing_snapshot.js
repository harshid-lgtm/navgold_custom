// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Amazon Pricing Snapshot', {
	refresh(frm) {
		if (frm.is_new()) {
			frm.dashboard.set_headline(
				__(
					'Save this snapshot, then click <b>Capture Live Prices</b> to pull the live min/max from Amazon.',
				),
			);
			return;
		}

		if (!cint(frm.doc.item_count)) {
			frm
				.add_custom_button(__('Capture Live Prices'), async () => {
					const confirmed = await navgold.confirm_dialog(
						__(
							'Pull the live min/max and selling price from Amazon for every active SKU into this snapshot?',
						),
					);
					if (!confirmed) return;
					await frm.call('capture_now');
					frappe.show_alert({
						message: __(
							'Live capture started from Amazon — this page refreshes when it lands.',
						),
						indicator: 'blue',
					});
					frappe.realtime.on('amazon_snapshot_captured', (data) => {
						if (data.name !== frm.doc.name) return;
						frappe.realtime.off('amazon_snapshot_captured');
						frappe.realtime.off('amazon_snapshot_capture_failed');
						frappe.msgprint(
							__('Captured {0} SKUs ({1})', [
								data.item_count,
								data.price_source,
							]),
						);
						frm.reload_doc();
					});
					frappe.realtime.on('amazon_snapshot_capture_failed', (data) => {
						if (data.name !== frm.doc.name) return;
						frappe.realtime.off('amazon_snapshot_captured');
						frappe.realtime.off('amazon_snapshot_capture_failed');
						frappe.msgprint({
							title: __('Live capture failed'),
							indicator: 'red',
							message: __('Could not pull live prices from Amazon: {0}', [
								data.error || __('unknown error'),
							]),
						});
					});
				})
				.addClass('btn-primary');
			refresh_preview(frm);
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
			frappe.msgprint(
				__('Reverted {0} SKUs ({1} skipped)', [
					message.reverted,
					message.skipped,
				]),
			);
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

		if (frm.doc.submission_log) {
			frm.add_custom_button(__('Open Rendered Feed'), () =>
				frappe.set_route(
					'Form',
					'Amazon Pricing Submission Log',
					frm.doc.submission_log,
				),
			);
		}

		refresh_preview(frm);
		refresh_variance(frm);
	},
});

function refresh_variance(frm) {
	if (!frm.doc.variance_data) return;
	let variance = {};
	try {
		variance = JSON.parse(frm.doc.variance_data);
	} catch {
		return;
	}
	const summary = variance.summary || {};
	const flagged = (variance.rows || []).filter((row) => row.status !== 'OK');

	const body = flagged
		.map(
			(row) => `<tr>
				<td>${frappe.utils.escape_html(row.proxy_sku || '')}</td>
				<td class="text-right">${
					row.prev_min == null ? '—' : format_currency(row.prev_min, 'GBP')
				}</td>
				<td class="text-right">${format_currency(row.min_price || 0, 'GBP')}</td>
				<td class="text-right" style="color:${get_delta_colour(
					row.min_delta_pct,
				)}">${format_delta(row.min_delta_pct)}</td>
				<td class="text-right">${format_currency(row.max_price || 0, 'GBP')}</td>
				<td class="text-right" style="color:${get_delta_colour(
					row.max_delta_pct,
				)}">${format_delta(row.max_delta_pct)}</td>
				<td>${frappe.utils.escape_html(row.status || '')}</td>
			</tr>`,
		)
		.join('');

	const wrapper = frm.get_field('preview_html').$wrapper;
	wrapper.prepend(`
		<div style="margin-bottom:14px">
			<div class="text-muted small" style="margin-bottom:6px">
				Variance vs ${
					summary.compared_against
						? frappe.utils.escape_html(summary.compared_against)
						: 'no prior snapshot'
				}
				— ${cint(summary.movers)} mover(s) over ${flt(summary.threshold_pct)}%,
				${cint(summary.new)} new, ${cint(summary.dropped)} dropped of ${cint(
					summary.total,
				)} SKUs
			</div>
			<div style="max-height:300px;overflow:auto">
				<table class="table table-bordered" style="font-size:12.5px">
					<thead><tr>
						<th>SKU</th><th class="text-right">Prev Min</th><th class="text-right">Min</th>
						<th class="text-right">Δ%</th><th class="text-right">Max</th><th class="text-right">Δ%</th><th>Status</th>
					</tr></thead>
					<tbody>${
						body ||
						'<tr><td colspan="7" class="text-center text-muted">No flagged movers</td></tr>'
					}</tbody>
				</table>
			</div>
		</div>
	`);
}

function get_delta_colour(pct) {
	if (flt(pct) > 0) return '#b94a48';
	if (flt(pct) < 0) return '#3c763d';
	return 'inherit';
}

function format_delta(pct) {
	const value = flt(pct);
	if (!value) return '0%';
	return `${value > 0 ? '+' : ''}${value}%`;
}

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
	frappe.msgprint(__('Published to {0}: {1}', [channel, count]));
}

function refresh_preview(frm) {
	const wrapper = frm.get_field('preview_html').$wrapper;
	let rows = [];
	try {
		rows = JSON.parse(frm.doc.snapshot_data || '[]');
	} catch {
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
				<td class="text-right">${
					is_null(row.live_selling_price)
						? '—'
						: format_currency(row.live_selling_price, 'GBP')
				}</td>
				<td class="text-center">${row.fixed_price ? '✓' : ''}</td>
				<td>${frappe.utils.escape_html(row.system_status || '')}</td>
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
					<th class="text-right">Max</th><th class="text-right">Selling</th>
					<th class="text-center">Fixed</th><th>In System</th>
				</tr></thead>
				<tbody>${
					body ||
					'<tr><td colspan="8" class="text-center text-muted">Empty snapshot</td></tr>'
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
