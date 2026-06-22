frappe.pages['amazon-control-center'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Amazon Control Center',
		single_column: true,
	});

	const control_center = new AmazonControlCenter(page);
	control_center.refresh();

	page.set_primary_action('Refresh', () => control_center.refresh(), 'refresh');
	wrapper.amazon_control_center = control_center;
};

class AmazonControlCenter {
	constructor(page) {
		this.page = page;
		this.body = $('<div class="acc-body">').appendTo(page.main);
		this.auto_timer = null;
		this.add_styles();
		this.add_auto_refresh_toggle();
	}

	add_auto_refresh_toggle() {
		this.page.add_inner_button('Auto-refresh: Off', () => this.toggle_auto());
	}

	toggle_auto() {
		if (this.auto_timer) {
			clearInterval(this.auto_timer);
			this.auto_timer = null;
			this.page.set_indicator('Auto-refresh off', 'gray');
		} else {
			this.auto_timer = setInterval(() => this.refresh(true), 60000);
			this.page.set_indicator('Auto-refresh on (60s)', 'green');
		}
	}

	async refresh(silent) {
		if (!silent) this.body.html('<div class="text-muted" style="padding:2rem">Loading control center…</div>');
		try {
			const { message } = await frappe.call(
				'navgold_custom.navgold_custom.amazon_pricing.control_center.get_control_center_data'
			);
			this.data = message;
			this.render();
		} catch (e) {
			this.body.html('<div class="text-danger" style="padding:2rem">Failed to load control center.</div>');
		}
	}

	render() {
		const d = this.data;
		const r = d.readiness;
		const blockers = r.blocked_total;
		const below = d.below_min.count;
		const feed_errors = d.feeds.status_counts.Error;
		const frozen = d.switches.freeze_amazon_outbound;

		const is_go = blockers === 0 && below === 0 && feed_errors === 0;

		this.body.empty();
		this.body.append(this.banner(is_go, blockers, below, feed_errors, frozen));
		this.body.append(this.fire_strip(d));
		this.body.append(this.push_status_strip(d.push_status));
		this.body.append(this.switches_bar(d.switches));
		this.body.append(this.stat_cards(d));
		this.body.append(this.category_grid(r));

		this.body.append(this.quick_actions(d));

		const tables = $('<div class="acc-tables">').appendTo(this.body);
		tables.append(this.offenders_table(r.top_offenders));
		tables.append(this.below_min_table(d.below_min));
		tables.append(this.feeds_table(d.feeds.recent));
		tables.append(this.suppliers_table(d.suppliers));

		this.body.append(this.safeguards_panel(d));
		this.body.append(this.nav_panel());

		this.body.append(
			`<div class="text-muted small" style="margin-top:1rem">Generated: ${d.generated_on}</div>`
		);
	}

	banner(is_go, blockers, below, feed_errors, frozen) {
		const cls = is_go ? 'acc-go' : 'acc-nogo';
		const label = is_go ? 'GO — all clear' : 'NO-GO — issues present';
		const reasons = [];
		if (blockers) reasons.push(`${blockers} SKUs would be blocked`);
		if (below) reasons.push(`${below} below-min sales`);
		if (feed_errors) reasons.push(`${feed_errors} feed errors`);
		const sub = reasons.length ? reasons.join(' · ') : 'No SKU blocked, no below-min sales, no feed errors';

		const freeze_btn = frozen
			? `<button class="btn btn-sm btn-success acc-freeze" data-freeze="0">Outbound is FROZEN — click to release</button>`
			: `<button class="btn btn-sm btn-danger acc-freeze" data-freeze="1">FREEZE all Amazon/Repricer outbound</button>`;

		const wrap = $(`
			<div class="acc-banner ${cls}">
				<div>
					<div class="acc-banner-title">${label}</div>
					<div class="acc-banner-sub">${sub}</div>
				</div>
				<div>${freeze_btn}</div>
			</div>
		`);

		wrap.find('.acc-freeze').on('click', (e) => this.toggle_freeze($(e.currentTarget).data('freeze')));
		return wrap;
	}

	async toggle_freeze(freeze) {
		const verb = freeze ? 'FREEZE all outbound writes to Amazon and Repricer' : 'RELEASE the outbound freeze';
		const ok = await new Promise((res) =>
			frappe.confirm(`Are you sure you want to ${verb}?`, () => res(true), () => res(false))
		);
		if (!ok) return;
		await frappe.call(
			'navgold_custom.navgold_custom.amazon_pricing.control_center.set_outbound_freeze',
			{ freeze }
		);
		frappe.show_alert({ message: freeze ? 'Outbound frozen' : 'Outbound released', indicator: freeze ? 'red' : 'green' });
		this.refresh();
	}

	fire_strip(d) {
		const v = (d.below_min && d.below_min.velocity) || {};
		const cell = (label, win) => {
			const c = win || { count: 0, loss: 0 };
			const hot = c.loss > 0;
			return `<div class="acc-fire-cell ${hot ? 'hot' : 'cool'}">
				<div class="acc-fire-val">${format_currency(c.loss || 0, 'GBP')}</div>
				<div class="acc-fire-label">${label} · ${c.count || 0} sales</div>
			</div>`;
		};

		const snap = d.last_good_snapshot;
		const rollback = snap
			? `<button class="btn btn-sm btn-danger acc-rollback">↩ Revert to last good prices &amp; publish</button>
				<div class="acc-fire-snap">last good: ${snap.name} (${snap.item_count} SKUs)</div>`
			: `<div class="acc-fire-snap text-muted">No snapshot to roll back to — take one.</div>`;

		const on_fire = (v['15m'] && v['15m'].loss > 0) || (v['1h'] && v['1h'].loss > 0);
		const wrap = $(`
			<div class="acc-fire ${on_fire ? 'burning' : ''}">
				<div class="acc-fire-title">💷 Loss velocity ${on_fire ? '— BLEEDING NOW' : '— quiet'}</div>
				<div class="acc-fire-grid">
					${cell('last 15 min', v['15m'])}
					${cell('last 1 hour', v['1h'])}
					${cell('last 24 hours', v['24h'])}
				</div>
				<div class="acc-fire-actions">${rollback}</div>
			</div>
		`);
		wrap.find('.acc-rollback').on('click', () => this.emergency_rollback(snap));
		return wrap;
	}

	async emergency_rollback(snap) {
		const ok = await new Promise((res) =>
			frappe.confirm(
				`EMERGENCY ROLLBACK: restore prices from <b>${snap.name}</b> (${snap.item_count} SKUs) and re-publish to Repricer? Guards still apply.`,
				() => res(true), () => res(false))
		);
		if (!ok) return;
		frappe.show_alert({ message: 'Rolling back…', indicator: 'orange' });
		try {
			const { message } = await frappe.call({
				method: 'navgold_custom.navgold_custom.amazon_pricing.control_center.emergency_rollback',
				args: { channel: 'Repricer' },
			});
			frappe.msgprint(
				`Rolled back ${message.revert.reverted} SKUs from ${message.snapshot} and published to Repricer.`
			);
			this.refresh();
		} catch (e) {
			frappe.show_alert({ message: 'Rollback failed — check Error Log', indicator: 'red' });
		}
	}

	push_status_strip(ps) {
		if (!ps) return $('');
		const pill = (label, on, on_text, off_text) =>
			`<span class="acc-pill ${on ? 'live' : 'off'}">${label}: <b>${on ? (on_text || 'YES') : (off_text || 'NO')}</b></span>`;

		// Prices-to-Amazon is the money-risk line: green when NOT sending, amber when live.
		const amazon_prices = ps.amazon_prices_live
			? `<span class="acc-pill warn">Prices → Amazon: <b>LIVE</b></span>`
			: `<span class="acc-pill safe">Prices → Amazon: <b>NOT SENDING</b></span>`;

		const tone = ps.frozen ? 'frozen' : ps.amazon_prices_live ? 'warn' : 'safe';
		return $(`
			<div class="acc-push ${tone}">
				<div class="acc-push-headline">Outbound: ${frappe.utils.escape_html(ps.headline)}</div>
				<div class="acc-pills">
					${amazon_prices}
					${pill('Stock → Amazon', ps.amazon_stock_live, 'SENDING', 'OFF')}
					${pill('Min/Max → Repricer', ps.repricer_live, 'LIVE', 'OFF')}
					<span class="acc-pill neutral">Amazon mode: <b>${frappe.utils.escape_html(ps.amazon_mode)}</b></span>
					${pill('Auto-push', ps.auto_on, 'ON', 'OFF')}
				</div>
			</div>
		`);
	}

	switches_bar(s) {
		const chip = (label, on, on_text, off_text) =>
			`<span class="acc-chip ${on ? 'on' : 'off'}">${label}: <b>${on ? (on_text || 'On') : (off_text || 'Off')}</b></span>`;
		return $(`
			<div class="acc-chips">
				${chip('Amazon mode', s.amazon_publishing_mode === 'Stock Only', s.amazon_publishing_mode, s.amazon_publishing_mode)}
				${chip('Pricing rollout', s.pricing_publishing_mode !== 'Publish All Prices', s.pricing_publishing_mode, s.pricing_publishing_mode)}
				${chip('Order sync', s.order_sync_enabled)}
				${chip('AFN stock sync', s.enable_afn_stock_sync)}
				${chip('Repricer', s.repricer_enabled)}
				${chip('Send min to Amazon', s.send_min_price_to_amazon)}
				<span class="acc-chip neutral">Deviation guard: <b>${s.price_change_threshold_percent || 0}% / ${s.price_change_block_direction}</b></span>
				<span class="acc-chip neutral">Last price sync: <b>${s.last_pricing_synced_at || '—'}</b></span>
			</div>
		`);
	}

	stat_cards(d) {
		const r = d.readiness;
		const orders = (d.orders.summary || []).reduce((m, x) => ((m[x.label] = x), m), {});
		const open_failed = orders['Open Failed Syncs'];
		const mins = orders['Minutes Since Last Amazon Order'];

		const cards = [
			this.card('Active SKUs', r.total_active, 'neutral'),
			this.card('Ready to push', r.ok_total, r.ok_total === r.total_active ? 'good' : 'neutral'),
			this.card('Would be BLOCKED', r.blocked_total, r.blocked_total ? 'bad' : 'good'),
			this.card(`Below-min sales (${d.below_min.days_back}d)`, d.below_min.count, d.below_min.count ? 'bad' : 'good',
				d.below_min.estimated_loss ? `≈ ${format_currency(d.below_min.estimated_loss, 'GBP')} loss` : ''),
			this.card('Feed errors', d.feeds.status_counts.Error, d.feeds.status_counts.Error ? 'bad' : 'good',
				`${d.feeds.status_counts.Pending} pending`),
			this.card('Open failed order syncs', open_failed ? open_failed.value : 0,
				(open_failed && open_failed.indicator === 'Red') ? 'bad' : 'good'),
			this.card('Mins since last order', mins ? mins.value : '—',
				(mins && mins.indicator === 'Red') ? 'bad' : 'good'),
			this.card('Stale suppliers', d.suppliers.stale_count, d.suppliers.stale_count ? 'warn' : 'good',
				`of ${d.suppliers.total}`),
		];
		return $('<div class="acc-cards">').append(cards);
	}

	card(label, value, tone, sub) {
		return $(`
			<div class="acc-card ${tone}">
				<div class="acc-card-value">${value}</div>
				<div class="acc-card-label">${label}</div>
				${sub ? `<div class="acc-card-sub">${sub}</div>` : ''}
			</div>
		`);
	}

	category_grid(r) {
		if (!r.blocked_total) return $('');
		const items = Object.keys(r.categories)
			.filter((k) => r.categories[k] > 0)
			.map((k) => `<span class="acc-chip bad">${r.category_labels[k]}: <b>${r.categories[k]}</b></span>`)
			.join('');
		return $(`<div class="acc-section"><div class="acc-section-title">Why SKUs are blocked</div><div class="acc-chips">${items}</div></div>`);
	}

	offenders_table(rows) {
		const body = rows.map((row) => `
			<tr>
				<td>${frappe.utils.escape_html(row.proxy_sku || '')}</td>
				<td>${frappe.utils.escape_html(row.active_strategy || '')}</td>
				<td class="text-right">${format_currency(row.min_price || 0, 'GBP')}</td>
				<td class="text-right">${format_currency(row.max_price || 0, 'GBP')}</td>
				<td class="text-right ${flt(row.purchase_cost) <= 0 ? 'text-danger' : ''}">${format_currency(row.purchase_cost || 0, 'GBP')}</td>
				<td class="text-danger small">${frappe.utils.escape_html(row.violations || '')}</td>
			</tr>`).join('');
		return this.table_block('Top blocked SKUs', ['SKU', 'Strategy', 'Min', 'Max', 'Purchase Cost', 'Violations'], body,
			'Amazon Pricing Push Readiness');
	}

	below_min_table(below) {
		const body = below.rows.map((row) => `
			<tr>
				<td>${frappe.utils.escape_html(row.proxy_sku || row.item_code || '')}</td>
				<td><a href="/app/sales-order/${encodeURIComponent(row.sales_order)}">${frappe.utils.escape_html(row.sales_order || '')}</a></td>
				<td class="text-right">${flt(row.qty)}</td>
				<td class="text-right">${format_currency(row.rate || 0, 'GBP')}</td>
				<td class="text-right">${format_currency(row.min_price || 0, 'GBP')}</td>
				<td class="text-right text-danger">${format_currency(row.estimated_loss || 0, 'GBP')}</td>
			</tr>`).join('');
		return this.table_block(`Below-min sales (${below.days_back}d)`, ['SKU', 'Sales Order', 'Qty', 'Sold At', 'Min', 'Est. Loss'], body,
			'Below Min Price Sales');
	}

	feeds_table(rows) {
		const ind = (s) => (s === 'Error' ? 'text-danger' : s === 'Completed' || s === 'Submitted' ? 'text-success' : '');
		const body = rows.map((row) => `
			<tr>
				<td>${frappe.utils.escape_html(row.submission_type || '')}</td>
				<td class="${ind(row.status)}">${frappe.utils.escape_html(row.status || '')}</td>
				<td>${frappe.utils.escape_html(row.amazon_status || '')}</td>
				<td class="text-right">${row.blocked_count || 0}</td>
				<td class="small text-muted">${frappe.datetime.str_to_user(row.creation)}</td>
			</tr>`).join('');
		return this.table_block('Recent feed submissions', ['Type', 'Status', 'Amazon', 'Blocked', 'When'], body,
			'Amazon Pricing Push Monitor');
	}

	suppliers_table(suppliers) {
		const body = suppliers.rows.map((row) => `
			<tr>
				<td>${frappe.utils.escape_html(row.supplier || '')}</td>
				<td class="text-right">${row.item_count || 0}</td>
				<td class="text-right">${row.in_stock_count || 0}</td>
				<td class="text-right text-warning">${row.days_stale}</td>
			</tr>`).join('');
		return this.table_block('Stale suppliers (>7d)', ['Supplier', 'Items', 'In stock', 'Days stale'], body,
			'Supplier Data Readiness');
	}

	quick_actions(d) {
		const wrap = $('<div class="acc-section"><div class="acc-section-title">Quick actions</div><div class="acc-actions"></div></div>');
		const row = wrap.find('.acc-actions');
		const btn = (label, cls, fn) => {
			const b = $(`<button class="btn btn-sm ${cls}">${label}</button>`);
			b.on('click', fn);
			row.append(b);
		};

		const alerts = d.alerts || {};
		const alert_state = !alerts.telegram_enabled
			? '🔕 Alerts OFF'
			: alerts.telegram_configured
				? '🔔 Alerts ON'
				: '⚠️ Alerts: chat not set';

		btn('📸 Take pricing snapshot', 'btn-primary', () => this.take_snapshot());
		btn('↻ Refresh strategies', 'btn-default', () =>
			this.run_action('navgold_custom.navgold_custom.amazon_pricing.strategy_selector.bulk_refresh_strategies',
				{}, 'Re-select active strategy for all SKUs from current stock?'));
		btn('⬇ Fetch AFN stock', 'btn-default', () =>
			this.run_action('navgold_custom.navgold_custom.amazon_pricing.afn_stock_fetcher.bulk_fetch_afn_stock',
				{}, 'Pull FBA fulfillable stock from Amazon into ERP now?'));
		btn('📨 Send digest now', 'btn-default', () =>
			this.run_action('navgold_custom.navgold_custom.amazon_pricing.alerts.send_digest', {},
				'Send the rollup digest to Telegram now?'));
		btn('🧪 Test alert', 'btn-default', () =>
			this.run_action('navgold_custom.navgold_custom.amazon_pricing.alerts.send_test', {},
				'Send a Telegram test message?'));

		row.append(`<a class="btn btn-sm btn-default" href="/app/navgold-settings">⚙ Navgold Settings</a>`);
		row.append(`<a class="btn btn-sm btn-default" href="/app/amazon-sp-api-settings">🔌 SP-API Settings</a>`);
		row.append(`<a class="btn btn-sm btn-default" href="/app/amazon-pricing-control">🗂 Reports Workspace</a>`);
		row.append(`<span class="acc-chip neutral" style="align-self:center">${alert_state}</span>`);
		return wrap;
	}

	async take_snapshot() {
		const ok = await new Promise((res) =>
			frappe.confirm('Capture a pricing snapshot of all active SKUs now?', () => res(true), () => res(false))
		);
		if (!ok) return;
		const { message } = await frappe.call({
			method: 'navgold_custom.navgold_custom.doctype.amazon_pricing_snapshot.amazon_pricing_snapshot.take_snapshot_now',
		});
		frappe.show_alert({ message: `Snapshot ${message.name} captured`, indicator: 'green' });
		frappe.set_route('Form', 'Amazon Pricing Snapshot', message.name);
	}

	async run_action(method, args, confirm_text) {
		if (confirm_text) {
			const ok = await new Promise((res) => frappe.confirm(confirm_text, () => res(true), () => res(false)));
			if (!ok) return;
		}
		try {
			await frappe.call({ method, args });
			frappe.show_alert({ message: 'Done', indicator: 'green' });
			this.refresh(true);
		} catch (e) {
			frappe.show_alert({ message: 'Action failed', indicator: 'red' });
		}
	}

	safeguards_panel(d) {
		const s = d.switches;
		const guard = (name, ok, detail) =>
			`<div class="acc-guard"><span class="acc-dot ${ok ? 'good' : 'bad'}"></span>
				<span class="acc-guard-name">${name}</span>
				<span class="acc-guard-detail">${detail}</span></div>`;

		const rows = [
			guard('Zero-cost / invalid push block', true, 'Active — validates every push'),
			guard('Price-deviation guard', s.price_change_threshold_percent > 0,
				`${s.price_change_threshold_percent || 0}% / ${s.price_change_block_direction}`),
			guard('Amazon Stock-Only mode', s.amazon_publishing_mode === 'Stock Only', s.amazon_publishing_mode),
			guard('Phased rollout (canary)', s.pricing_publishing_mode !== 'Publish All Prices', s.pricing_publishing_mode),
			guard('Outbound freeze switch', true, s.freeze_amazon_outbound ? 'FROZEN' : 'armed (not frozen)'),
			guard('AFN stock sync', s.enable_afn_stock_sync, s.enable_afn_stock_sync ? 'On' : 'Off'),
			guard('Feed-status poller', true, 'every 15 min'),
			guard('Telegram alerts', (d.alerts || {}).telegram_enabled && (d.alerts || {}).telegram_configured,
				!(d.alerts || {}).telegram_enabled ? 'Off' : (d.alerts || {}).telegram_configured ? 'On' : 'chat not set'),
		];
		return $(`<div class="acc-section"><div class="acc-section-title">Safeguards &amp; fallbacks</div>
			<div class="acc-guards">${rows.join('')}</div></div>`);
	}

	nav_panel() {
		const report = (name) =>
			`<a class="acc-link" href="/app/query-report/${encodeURIComponent(name)}">${name}</a>`;
		const doc = (label, route) => `<a class="acc-link" href="/app/${route}">${label}</a>`;

		const groups = [
			['Go-live readiness reports', [
				report('Amazon Go-Live Data Readiness'),
				report('Amazon Pricing Push Readiness'),
				report('Supplier Data Readiness'),
				report('Amazon Strategy Price Comparison'),
			]],
			['Live monitoring reports', [
				report('Below Min Price Sales'),
				report('Amazon Pricing Push Monitor'),
				report('Amazon Order Sync Health'),
			]],
			['Pricing & cost reports', [
				report('Amazon Purchase Cost Overview'),
				report('Amazon Min Max Report'),
			]],
			['Operational reports', [
				report('To Purchase'),
				report('Navgold Forecast Report'),
				report('Navgold Stock Summary'),
				report('FBA Transfer Tracker'),
			]],
			['Key records', [
				doc('Amazon Product Pricing', 'amazon-product-pricing'),
				doc('Amazon Product Strategy', 'amazon-product-strategy'),
				doc('Proxy SKU', 'proxy-sku'),
				doc('Pricing Submission Log', 'amazon-pricing-submission-log'),
				doc('Failed Sync Records', 'amazon-failed-sync-record'),
				doc('Supplier Item Price and Stock', 'supplier-item-price-and-stock'),
			]],
		];

		const cols = groups.map(([title, links]) =>
			`<div class="acc-nav-col"><div class="acc-nav-title">${title}</div>${links.join('')}</div>`
		).join('');
		return $(`<div class="acc-section"><div class="acc-section-title">Jump to everything</div>
			<div class="acc-nav">${cols}</div></div>`);
	}

	table_block(title, headers, body_html, report_name) {
		const head = headers.map((h) => `<th>${h}</th>`).join('');
		const link = report_name
			? `<a class="acc-open" href="/app/query-report/${encodeURIComponent(report_name)}">Open full report →</a>`
			: '';
		return $(`
			<div class="acc-table-card">
				<div class="acc-table-head"><span>${title}</span>${link}</div>
				<div class="acc-table-scroll">
					<table class="table table-bordered acc-table">
						<thead><tr>${head}</tr></thead>
						<tbody>${body_html || `<tr><td colspan="${headers.length}" class="text-muted text-center">None 🎉</td></tr>`}</tbody>
					</table>
				</div>
			</div>
		`);
	}

	add_styles() {
		if (document.getElementById('acc-styles')) return;
		const css = `
			.acc-body { padding: 0 4px 2rem; }
			.acc-banner { display:flex; justify-content:space-between; align-items:center; padding:18px 22px; border-radius:10px; margin:12px 0; color:#fff; }
			.acc-banner.acc-go { background:#1a7f4b; }
			.acc-banner.acc-nogo { background:#b3261e; }
			.acc-banner-title { font-size:20px; font-weight:700; }
			.acc-banner-sub { opacity:.9; font-size:13px; margin-top:2px; }
			.acc-fire { border-radius:10px; padding:12px 16px; margin:10px 0; border:1px solid #e6e9ec; background:#fafbfc; }
			.acc-fire.burning { background:#fff4f3; border-color:#f3c9c7; }
			.acc-fire-title { font-weight:700; font-size:14px; margin-bottom:8px; }
			.acc-fire.burning .acc-fire-title { color:#b3261e; }
			.acc-fire-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; max-width:560px; }
			.acc-fire-cell { border-radius:8px; padding:10px 12px; border:1px solid #e6e9ec; background:#fff; }
			.acc-fire-cell.hot { background:#fdecec; border-color:#f3c9c7; }
			.acc-fire-val { font-size:20px; font-weight:700; }
			.acc-fire-cell.hot .acc-fire-val { color:#b3261e; }
			.acc-fire-label { font-size:11px; color:#6b7280; margin-top:2px; }
			.acc-fire-actions { margin-top:10px; display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
			.acc-fire-snap { font-size:11px; color:#8a929b; }
			.acc-push { border-radius:10px; padding:12px 16px; margin:10px 0; border:1px solid #e6e9ec; }
			.acc-push.safe { background:#f1f9f4; border-color:#bfe6d0; }
			.acc-push.warn { background:#fff7e8; border-color:#f3dca0; }
			.acc-push.frozen { background:#fdecec; border-color:#f3c9c7; }
			.acc-push-headline { font-weight:700; font-size:15px; margin-bottom:8px; }
			.acc-pills { display:flex; flex-wrap:wrap; gap:8px; }
			.acc-pill { font-size:12px; padding:4px 10px; border-radius:14px; background:#eef0f2; border:1px solid #dfe3e6; }
			.acc-pill.live { background:#fff2dd; border-color:#f0d091; }
			.acc-pill.warn { background:#fde9c9; border-color:#eec078; color:#92560a; }
			.acc-pill.safe { background:#e4f5ec; border-color:#bfe6d0; color:#176a3d; }
			.acc-pill.off { background:#eef0f2; color:#6b7280; }
			.acc-pill.neutral { background:#eef2f7; }
			.acc-chips { display:flex; flex-wrap:wrap; gap:8px; margin:10px 0; }
			.acc-chip { font-size:12px; padding:4px 10px; border-radius:14px; background:#eef0f2; border:1px solid #dfe3e6; }
			.acc-chip.on { background:#e4f5ec; border-color:#bfe6d0; }
			.acc-chip.off { background:#fdecec; border-color:#f3c9c7; }
			.acc-chip.bad { background:#fdecec; border-color:#f3c9c7; color:#9b1c17; }
			.acc-chip.neutral { background:#eef2f7; }
			.acc-cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:14px 0; }
			.acc-card { border:1px solid #e6e9ec; border-radius:10px; padding:14px 16px; background:#fff; }
			.acc-card-value { font-size:26px; font-weight:700; line-height:1; }
			.acc-card-label { font-size:12px; color:#6b7280; margin-top:6px; }
			.acc-card-sub { font-size:11px; color:#9aa3ad; margin-top:3px; }
			.acc-card.good .acc-card-value { color:#1a7f4b; }
			.acc-card.bad .acc-card-value { color:#b3261e; }
			.acc-card.warn .acc-card-value { color:#b7791f; }
			.acc-section { margin:14px 0; }
			.acc-section-title, .acc-table-head span { font-weight:600; }
			.acc-tables { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:10px; }
			@media (max-width:1100px){ .acc-tables { grid-template-columns:1fr; } }
			.acc-table-card { border:1px solid #e6e9ec; border-radius:10px; background:#fff; overflow:hidden; }
			.acc-table-head { display:flex; justify-content:space-between; align-items:center; padding:10px 14px; border-bottom:1px solid #eef0f2; }
			.acc-open { font-size:12px; }
			.acc-table-scroll { max-height:330px; overflow:auto; }
			.acc-table { margin:0; font-size:12.5px; }
			.acc-table th { position:sticky; top:0; background:#f7f8fa; z-index:1; }
			.acc-actions { display:flex; flex-wrap:wrap; gap:8px; }
			.acc-guards { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:8px 18px; }
			.acc-guard { display:flex; align-items:center; gap:8px; font-size:13px; padding:4px 0; }
			.acc-dot { width:9px; height:9px; border-radius:50%; flex:0 0 auto; }
			.acc-dot.good { background:#1a7f4b; }
			.acc-dot.bad { background:#b3261e; }
			.acc-guard-name { font-weight:600; }
			.acc-guard-detail { color:#6b7280; margin-left:auto; }
			.acc-nav { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:16px; }
			.acc-nav-title { font-weight:600; font-size:12px; text-transform:uppercase; color:#8a929b; margin-bottom:6px; }
			.acc-link { display:block; padding:3px 0; font-size:13px; }
		`;
		$(`<style id="acc-styles">${css}</style>`).appendTo(document.head);
	}
}
