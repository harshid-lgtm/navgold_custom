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

function get_checked_rows(frm) {
	const wrapper = frm.get_field('preview_html').$wrapper;
	return wrapper
		.find('tbody tr')
		.filter((index, element) => {
			const checkbox = element.querySelector('input.snapshot-row-check');
			return checkbox?.checked;
		})
		.get();
}

function get_selected_pricing(frm) {
	return get_checked_rows(frm)
		.map((element) => element.dataset.pricing)
		.filter(Boolean);
}

function get_edited_updates(frm) {
	return get_checked_rows(frm)
		.filter((element) => element.dataset.sku)
		.map((element) => ({
			sku: element.dataset.sku,
			min: flt(element.querySelector('input.snapshot-min').value),
			max: flt(element.querySelector('input.snapshot-max').value),
			our_price: flt(element.querySelector('input.snapshot-price').value),
		}));
}

async function publish(frm, channel) {
	if (channel === 'Repricer') return publish_to_repricer(frm);
	if (channel === 'Amazon') return push_edited_to_amazon(frm);
	frappe.msgprint(__('Unknown publish channel {0}', [channel]));
}

async function push_edited_to_amazon(frm) {
	const updates = get_edited_updates(frm);
	if (!updates.length) {
		frappe.msgprint(__('Tick at least one SKU in the preview to push.'));
		return;
	}
	// Dry run first so a reviewed count (after the publishing allowlist) precedes a real push.
	const dry = await frm.call('publish_edited', {
		updates: JSON.stringify(updates),
		live: 0,
	});
	const confirmed = await navgold.confirm_dialog(
		__(
			'Push {0} SKU(s) with your edited min/max/selling prices directly to Amazon? Fat-finger and freeze guards still apply.',
			[dry.message.sku_count],
		),
	);
	if (!confirmed) return;
	const { message } = await frm.call('publish_edited', {
		updates: JSON.stringify(updates),
		live: 1,
	});
	frappe.msgprint(
		__('Pushed {0} SKU(s) to Amazon (feed {1}).', [
			message.sku_count,
			message.feed_id || '—',
		]),
	);
	frm.reload_doc();
}

async function publish_to_repricer(frm) {
	const pricing_names = get_selected_pricing(frm);
	if (!pricing_names.length) {
		frappe.msgprint(__('Tick at least one SKU in the preview to publish.'));
		return;
	}
	const confirmed = await navgold.confirm_dialog(
		__(
			'Sync {0} selected SKU(s) to Repricer using their current ERP prices? Table edits apply to the Amazon push only.',
			[pricing_names.length],
		),
	);
	if (!confirmed) return;
	const { message } = await frm.call('publish', {
		channel: 'Repricer',
		pricing_names,
	});
	const count =
		message.synced ?? message.message_count ?? message.submitted ?? '';
	frappe.msgprint(__('Published to Repricer: {0}', [count]));
}

function price_input(css_class, value) {
	return `<input type="number" step="0.01" min="0" class="form-control ${css_class} text-right" value="${flt(
		value,
	)}" style="height:24px;padding:2px 6px">`;
}

function refresh_preview(frm) {
	const wrapper = frm.get_field('preview_html').$wrapper;
	let rows = [];
	try {
		rows = JSON.parse(frm.doc.snapshot_data || '[]');
	} catch {
		rows = [];
	}

	const body = rows
		.map((row) => {
			const min_price = flt(row.min_price);
			const max_price = flt(row.max_price);
			// Pre-fill the selling price with the live price when it sits inside the band, else the floor.
			const selling =
				!is_null(row.live_selling_price) &&
				flt(row.live_selling_price) >= min_price &&
				flt(row.live_selling_price) <= max_price
					? flt(row.live_selling_price)
					: min_price;
			const sku = frappe.utils.escape_html(row.proxy_sku || '');
			return `<tr data-sku="${sku}" data-pricing="${frappe.utils.escape_html(
				row.pricing || '',
			)}">
				<td class="text-center"><input type="checkbox" class="snapshot-row-check" checked></td>
				<td>${sku}</td>
				<td>${frappe.utils.escape_html(row.active_strategy || '')}</td>
				<td>${price_input('snapshot-min', min_price)}</td>
				<td>${price_input('snapshot-max', max_price)}</td>
				<td>${price_input('snapshot-price', selling)}</td>
				<td class="text-center">${row.fixed_price ? '✓' : ''}</td>
				<td>${frappe.utils.escape_html(row.system_status || '')}</td>
			</tr>`;
		})
		.join('');

	wrapper.html(`
		<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
			<input type="text" class="form-control input-sm snapshot-search" placeholder="${__(
				'Search SKU…',
			)}" style="max-width:240px">
			<span class="text-muted small">${rows.length} ${__(
				'SKUs — edit min/max/selling inline; only ticked rows are pushed',
			)}</span>
		</div>
		<div style="max-height:460px;overflow:auto">
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

	// Check-all toggles only the currently visible rows so it stays intuitive under a search filter.
	wrapper.find('.snapshot-check-all').on('change', (event) => {
		wrapper
			.find('tbody tr:visible input.snapshot-row-check')
			.prop('checked', event.target.checked);
	});

	wrapper.find('.snapshot-search').on('keyup', (event) => {
		const term = (event.target.value || '').toLowerCase().trim();
		wrapper.find('tbody tr').each((index, element) => {
			const sku = (element.dataset.sku || '').toLowerCase();
			element.style.display = !term || sku.includes(term) ? '' : 'none';
		});
	});
}
