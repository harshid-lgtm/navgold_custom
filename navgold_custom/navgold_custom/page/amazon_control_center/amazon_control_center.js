frappe.pages['amazon-control-center'].on_page_load = (wrapper) => {
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
		this.incident_mode = false;
		this.title_timer = null;
		this.original_title = document.title;
		this.add_styles();
		this.add_auto_refresh_toggle();
	}

	add_auto_refresh_toggle() {
		this.page.add_inner_button('Auto-refresh: Off', () =>
			this.toggle_auto_refresh(),
		);
	}

	toggle_auto_refresh() {
		if (this.auto_timer) {
			clearInterval(this.auto_timer);
			this.auto_timer = null;
			this.page.set_indicator('Auto-refresh off', 'gray');
		} else {
			this.auto_timer = setInterval(() => this.refresh(true), 60000);
			this.page.set_indicator('Auto-refresh on (60s)', 'green');
		}
	}

	handle_incident_mode(bleeding) {
		if (bleeding && !this.incident_mode) {
			// Money is leaving NOW. Don't wait for a 60s manual refresh — force a fast
			// loop and raise an audible + title alarm so it can't be missed.
			this.incident_mode = true;
			if (this.auto_timer) clearInterval(this.auto_timer);
			this.auto_timer = setInterval(() => this.refresh(true), 10000);
			this.page.set_indicator('INCIDENT — bleeding, 10s refresh', 'red');
			this.start_alarm();
		} else if (!bleeding && this.incident_mode) {
			this.incident_mode = false;
			this.stop_alarm();
			if (this.auto_timer) {
				clearInterval(this.auto_timer);
				this.auto_timer = null;
			}
			this.page.set_indicator('Auto-refresh off', 'gray');
		}
		if (bleeding) this.beep();
	}

	start_alarm() {
		if (this.title_timer) return;
		let on = false;
		this.title_timer = setInterval(() => {
			on = !on;
			document.title = on ? '🔴 BLEEDING — Amazon prices' : this.original_title;
		}, 1000);
	}

	stop_alarm() {
		if (this.title_timer) {
			clearInterval(this.title_timer);
			this.title_timer = null;
		}
		document.title = this.original_title;
	}

	beep() {
		try {
			if (!this.audio_context) {
				this.audio_context = new (
					window.AudioContext || window.webkitAudioContext
				)();
			}
			const audio_context = this.audio_context;
			const oscillator = audio_context.createOscillator();
			const gain = audio_context.createGain();
			oscillator.connect(gain);
			gain.connect(audio_context.destination);
			oscillator.frequency.value = 880;
			gain.gain.value = 0.1;
			oscillator.start();
			oscillator.stop(audio_context.currentTime + 0.2);
		} catch {
			// Browser autoplay policy may block audio — the visual alarm still fires.
		}
	}

	summary_to_map(summary) {
		return (summary || []).reduce((map, row) => {
			map[row.label] = row;
			return map;
		}, {});
	}

	async refresh(silent) {
		if (!silent)
			this.body.html(
				'<div class="text-muted" style="padding:2rem">Loading control center…</div>',
			);
		try {
			const { message } = await frappe.call(
				'navgold_custom.navgold_custom.amazon_pricing.control_center.get_control_center_data',
			);
			this.data = message;
			this.refresh_body();
		} catch {
			this.body.html(
				'<div class="text-danger" style="padding:2rem">Failed to load control center.</div>',
			);
		}
	}

	refresh_body() {
		const data = this.data;
		const readiness = data.readiness;
		const blockers = readiness.blocked_total;
		const below = data.below_min.count;
		const feed_errors = data.feeds.status_counts.Error;
		const frozen = data.switches.freeze_amazon_outbound;

		// Below-min detection depends entirely on order sync. If it is off or stalled,
		// below === 0 is meaningless and a GO banner would be a lie — hard NO-GO instead.
		const orders = this.summary_to_map(data.orders.summary);
		const minutes_since_order = orders['Minutes Since Last Amazon Order'];
		const detection_blind =
			!data.switches.order_sync_enabled ||
			(minutes_since_order && minutes_since_order.indicator === 'Red');

		const is_go =
			blockers === 0 && below === 0 && feed_errors === 0 && !detection_blind;

		const bleeding = (data.below_min?.velocity?.['15m']?.loss || 0) > 0;
		this.handle_incident_mode(bleeding);

		this.body.empty();
		this.body.append(
			this.format_banner(
				is_go,
				blockers,
				below,
				feed_errors,
				frozen,
				detection_blind,
			),
		);
		this.body.append(this.format_fire_strip(data));
		this.body.append(this.format_system_status(data));
		this.body.append(this.format_stat_cards(data));
		this.body.append(this.format_category_grid(readiness));
		this.body.append(this.format_fba_stock_panel(data));

		this.body.append(this.format_quick_actions(data));

		const tables = $('<div class="acc-tables">').appendTo(this.body);
		tables.append(this.format_top_sellers_table(data.top_sellers));
		tables.append(this.format_offenders_table(readiness.top_offenders));
		tables.append(this.format_below_min_table(data.below_min));
		tables.append(this.format_feeds_table(data.feeds.recent));
		tables.append(this.format_fba_stock_table(data.fba_stock));

		this.body.append(this.format_nav_panel());

		this.body.append(
			`<div class="text-muted small" style="margin-top:1rem">Generated: ${data.generated_on}</div>`,
		);
	}

	format_banner(is_go, blockers, below, feed_errors, frozen, detection_blind) {
		const banner_class = is_go ? 'acc-go' : 'acc-nogo';
		const label = is_go ? 'GO — all clear' : 'NO-GO — issues present';
		const reasons = [];
		if (detection_blind)
			reasons.push('below-min detection BLIND — order sync stale/off');
		if (blockers) reasons.push(`${blockers} SKUs would be blocked`);
		if (below) reasons.push(`${below} below-min sales`);
		if (feed_errors) reasons.push(`${feed_errors} feed errors`);
		const subtitle = reasons.length
			? reasons.join(' · ')
			: 'No SKU blocked, no below-min sales, no feed errors';

		const freeze_button = frozen
			? `<button class="btn btn-sm btn-success acc-freeze" data-freeze="0">Outbound is FROZEN — click to release</button>`
			: `<button class="btn btn-sm btn-danger acc-freeze" data-freeze="1">FREEZE all Amazon/Repricer outbound</button>`;

		const wrapper = $(`
			<div class="acc-banner ${banner_class}">
				<div>
					<div class="acc-banner-title">${label}</div>
					<div class="acc-banner-sub">${subtitle}</div>
				</div>
				<div>${freeze_button}</div>
			</div>
		`);

		wrapper
			.find('.acc-freeze')
			.on('click', (event) =>
				this.toggle_freeze($(event.currentTarget).data('freeze')),
			);
		return wrapper;
	}

	async toggle_freeze(freeze) {
		const action = freeze
			? 'FREEZE all outbound writes to Amazon and Repricer'
			: 'RELEASE the outbound freeze';
		const confirmed = await navgold.confirm_dialog(
			`Are you sure you want to ${action}?`,
		);
		if (!confirmed) return;
		await frappe.call(
			'navgold_custom.navgold_custom.amazon_pricing.control_center.set_outbound_freeze',
			{ freeze },
		);
		frappe.msgprint(freeze ? 'Outbound frozen' : 'Outbound released');
		this.refresh();
	}

	format_fire_strip(data) {
		const velocity = data.below_min?.velocity || {};
		const cell = (label, window_data) => {
			const window_loss = window_data || { count: 0, loss: 0 };
			const hot = window_loss.loss > 0;
			return `<div class="acc-fire-cell ${hot ? 'hot' : 'cool'}">
				<div class="acc-fire-val">${format_currency(
					window_loss.loss || 0,
					'GBP',
				)}</div>
				<div class="acc-fire-label">${label} · ${window_loss.count || 0} sales</div>
			</div>`;
		};

		const snapshot = data.last_good_snapshot;
		const rollback = snapshot
			? `<button class="btn btn-sm btn-danger acc-contain">CONTAIN NOW — revert both channels &amp; freeze</button>
				<button class="btn btn-sm btn-default acc-rollback">Repricer only</button>
				<div class="acc-fire-snap">last good: ${snapshot.name} (${snapshot.item_count} SKUs)</div>`
			: `<div class="acc-fire-snap text-muted">No snapshot to roll back to — take one.</div>`;

		const on_fire =
			(velocity['15m'] && velocity['15m'].loss > 0) ||
			(velocity['1h'] && velocity['1h'].loss > 0);
		const wrapper = $(`
			<div class="acc-fire ${on_fire ? 'burning' : ''}">
				<div class="acc-fire-title">Loss velocity ${
					on_fire ? '— BLEEDING NOW' : '— quiet'
				}</div>
				<div class="acc-fire-grid">
					${cell('last 15 min', velocity['15m'])}
					${cell('last 1 hour', velocity['1h'])}
					${cell('last 24 hours', velocity['24h'])}
				</div>
				<div class="acc-fire-actions">${rollback}</div>
			</div>
		`);
		wrapper
			.find('.acc-rollback')
			.on('click', () => this.emergency_rollback(snapshot));
		wrapper.find('.acc-contain').on('click', () => this.contain_now(snapshot));
		return wrapper;
	}

	async contain_now(snapshot) {
		const confirmed = await navgold.confirm_dialog(
			`CONTAIN NOW: restore prices from <b>${snapshot.name}</b> (${snapshot.item_count} SKUs), re-publish to <b>Amazon AND Repricer</b>, then FREEZE all outbound. Proceed?`,
		);
		if (!confirmed) return;
		const { message } = await frappe.call({
			method:
				'navgold_custom.navgold_custom.amazon_pricing.control_center.contain_now',
		});
		frappe.msgprint(
			`Contained: reverted ${message.revert.reverted} SKUs from ${message.snapshot}, re-published to Amazon + Repricer, outbound now FROZEN.`,
		);
		this.refresh();
	}

	async emergency_rollback(snapshot) {
		const confirmed = await navgold.confirm_dialog(
			`EMERGENCY ROLLBACK: restore prices from <b>${snapshot.name}</b> (${snapshot.item_count} SKUs) and re-publish to Repricer? Guards still apply.`,
		);
		if (!confirmed) return;
		const { message } = await frappe.call({
			method:
				'navgold_custom.navgold_custom.amazon_pricing.control_center.emergency_rollback',
			args: { channel: 'Repricer' },
		});
		frappe.msgprint(
			`Rolled back ${message.revert.reverted} SKUs from ${message.snapshot} and published to Repricer.`,
		);
		this.refresh();
	}

	format_system_status(data) {
		const push_status = data.push_status;
		const switches = data.switches;
		const alerts = data.alerts || {};
		if (!push_status) return $('');

		const format_value = (text, kind) =>
			`<span class="acc-stat-val ${kind || ''}">${frappe.utils.escape_html(
				String(text),
			)}</span>`;
		const format_row = (label, value_html) =>
			`<span class="acc-stat-label">${label}</span>${value_html}`;
		const format_toggle = (flag, on_text = 'On', off_text = 'Off') =>
			format_value(flag ? on_text : off_text, flag ? 'on' : 'off');

		const telegram_value = !alerts.telegram_enabled
			? format_value('Off', 'off')
			: alerts.telegram_configured
			  ? format_value('On', 'on')
			  : format_value('chat not set', 'warn');

		const outbound = [
			format_row(
				'Prices → Amazon',
				format_value(
					push_status.amazon_prices_live ? 'LIVE' : 'NOT SENDING',
					push_status.amazon_prices_live ? 'warn' : 'safe',
				),
			),
			format_row(
				'Stock → Amazon',
				format_toggle(push_status.amazon_stock_live, 'SENDING', 'OFF'),
			),
			format_row(
				'Min/Max → Repricer',
				format_toggle(push_status.repricer_live, 'LIVE', 'OFF'),
			),
			format_row('Auto-push', format_toggle(push_status.auto_on, 'ON', 'OFF')),
		].join('');

		const sync = [
			format_row('Order sync', format_toggle(switches.order_sync_enabled)),
			format_row(
				'AFN stock sync',
				format_toggle(switches.enable_afn_stock_sync),
			),
			format_row('Feed-status poller', format_value('15 min', 'on')),
			format_row('Telegram alerts', telegram_value),
		].join('');

		const guards = [
			format_row('Zero-cost / invalid block', format_value('Active', 'on')),
			format_row(
				'Deviation guard',
				format_value(
					`${switches.price_change_threshold_percent || 0}% / ${
						switches.price_change_block_direction
					}`,
					'neutral',
				),
			),
			format_row(
				'Send min → Amazon',
				format_toggle(switches.send_min_price_to_amazon),
			),
			format_row(
				'Pricing rollout',
				format_value(switches.pricing_publishing_mode, 'neutral'),
			),
			format_row(
				'Amazon mode',
				format_value(switches.amazon_publishing_mode, 'neutral'),
			),
		].join('');

		const tone = push_status.frozen
			? 'frozen'
			: push_status.amazon_prices_live
			  ? 'warn'
			  : 'safe';
		return $(`
			<div class="acc-status ${tone}">
				<div class="acc-status-grid">
					<div class="acc-status-col">
						<div class="acc-status-title">Outbound now</div>
						${outbound}
					</div>
					<div class="acc-status-col">
						<div class="acc-status-title">Sync &amp; alerts</div>
						${sync}
					</div>
					<div class="acc-status-col">
						<div class="acc-status-title">Guards &amp; rollout</div>
						${guards}
					</div>
				</div>
				<div class="acc-status-foot">Last price sync: ${
					switches.last_pricing_synced_at || '—'
				}</div>
			</div>
		`);
	}

	format_stat_cards(data) {
		const readiness = data.readiness;
		const orders = this.summary_to_map(data.orders.summary);
		const open_failed = orders['Open Failed Syncs'];
		const minutes_since_order = orders['Minutes Since Last Amazon Order'];
		const fba_stock = this.summary_to_map(data.fba_stock.summary);
		const minutes_since_fba_sync =
			fba_stock['Minutes Since Last FBA Stock Sync'];

		const cards = [
			this.format_card('Active SKUs', readiness.total_active, 'neutral'),
			this.format_card(
				'Ready to push',
				readiness.ok_total,
				readiness.ok_total === readiness.total_active ? 'good' : 'neutral',
			),
			this.format_card(
				'Would be BLOCKED',
				readiness.blocked_total,
				readiness.blocked_total ? 'bad' : 'good',
			),
			this.format_card(
				`Below-min sales (${data.below_min.days_back}d)`,
				data.below_min.count,
				data.below_min.count ? 'bad' : 'good',
				data.below_min.estimated_loss
					? `≈ ${format_currency(data.below_min.estimated_loss, 'GBP')} loss`
					: '',
			),
			this.format_card(
				'Feed errors',
				data.feeds.status_counts.Error,
				data.feeds.status_counts.Error ? 'bad' : 'good',
				`${data.feeds.status_counts.Pending} pending`,
			),
			this.format_card(
				'Oldest open feed',
				data.feeds.oldest_open_minutes
					? `${data.feeds.oldest_open_minutes}m`
					: '—',
				data.feeds.oldest_open_minutes > 30 ? 'bad' : 'good',
				`${data.feeds.status_counts.Submitted} in flight`,
			),
			this.format_card(
				'Open failed order syncs',
				open_failed ? open_failed.value : 0,
				open_failed && open_failed.indicator === 'Red' ? 'bad' : 'good',
			),
			this.format_card(
				'Mins since last order',
				minutes_since_order ? minutes_since_order.value : '—',
				minutes_since_order && minutes_since_order.indicator === 'Red'
					? 'bad'
					: 'good',
			),
			this.format_card(
				'Mins since last FBA stock sync',
				minutes_since_fba_sync ? minutes_since_fba_sync.value : '—',
				minutes_since_fba_sync && minutes_since_fba_sync.indicator === 'Red'
					? 'bad'
					: 'good',
			),
		];
		return $('<div class="acc-cards">').append(cards);
	}

	format_card(label, value, tone, subtitle) {
		return $(`
			<div class="acc-card ${tone}">
				<div class="acc-card-value">${value}</div>
				<div class="acc-card-label">${label}</div>
				${subtitle ? `<div class="acc-card-sub">${subtitle}</div>` : ''}
			</div>
		`);
	}

	format_category_grid(readiness) {
		if (!readiness.blocked_total) return $('');
		const items = Object.keys(readiness.categories)
			.filter((key) => readiness.categories[key] > 0)
			.map(
				(key) =>
					`<span class="acc-chip bad">${readiness.category_labels[key]}: <b>${readiness.categories[key]}</b></span>`,
			)
			.join('');
		return $(
			`<div class="acc-section"><div class="acc-section-title">Why SKUs are blocked</div><div class="acc-chips">${items}</div></div>`,
		);
	}

	format_fba_stock_panel(data) {
		const summary = data.fba_stock?.summary || [];
		if (!summary.length) return $('');
		const tone_map = { Red: 'bad', Green: 'on', Blue: 'neutral', Gray: 'off' };
		const stale = summary.some(
			(item) =>
				item.label === 'Minutes Since Last FBA Stock Sync' &&
				item.indicator === 'Red',
		);
		const rows = summary
			.map(
				(item) =>
					`<span class="acc-status-label">${frappe.utils.escape_html(
						item.label,
					)}</span><span class="acc-stat-val ${
						tone_map[item.indicator] || 'neutral'
					}">${frappe.utils.escape_html(String(item.value))}</span>`,
			)
			.join('');
		return $(`
			<div class="acc-status ${stale ? 'warn' : 'safe'}">
				<div class="acc-status-col" style="max-width:560px">
					<div class="acc-status-title">FBA stock sync health</div>
					${rows}
				</div>
				<div class="acc-status-foot">
					<a class="acc-open" href="/app/query-report/${encodeURIComponent(
						'Amazon FBA Stock Sync Health',
					)}">Open full report →</a>
				</div>
			</div>
		`);
	}

	format_top_sellers_table(top_sellers) {
		const rows = top_sellers?.rows || [];
		const days_back = top_sellers?.days_back || 30;
		const sku_links = (skus) =>
			(skus || [])
				.map(
					(sku) =>
						`<a href="/app/proxy-sku/${encodeURIComponent(
							sku,
						)}">${frappe.utils.escape_html(sku)}</a>`,
				)
				.join(', ') || '<span class="text-muted"><i>unmapped</i></span>';
		const body = rows
			.map(
				(row) => `
			<tr>
				<td>${frappe.utils.escape_html(
					row.item_code || '',
				)} · ${frappe.utils.escape_html(row.uom || '')}</td>
				<td class="small">${sku_links(row.skus)}</td>
				<td class="text-right">${flt(row.total_qty)}</td>
				<td class="text-right">${format_currency(row.total_amount || 0, 'GBP')}</td>
				<td class="text-right">${cint(row.order_count)}</td>
			</tr>`,
			)
			.join('');
		return this.format_table_block(
			`Top selling SKUs (${days_back}d)`,
			['Item · UOM', 'Amazon SKU(s)', 'Qty', 'Revenue', 'Orders'],
			body,
			'Amazon Top Selling SKU',
		);
	}

	format_offenders_table(rows) {
		const body = rows
			.map(
				(row) => `
			<tr>
				<td>${frappe.utils.escape_html(row.proxy_sku || '')}</td>
				<td>${frappe.utils.escape_html(row.active_strategy || '')}</td>
				<td class="text-right">${format_currency(row.min_price || 0, 'GBP')}</td>
				<td class="text-right">${format_currency(row.max_price || 0, 'GBP')}</td>
				<td class="text-right ${
					flt(row.purchase_cost) <= 0 ? 'text-danger' : ''
				}">${format_currency(row.purchase_cost || 0, 'GBP')}</td>
				<td class="text-danger small">${frappe.utils.escape_html(
					row.violations || '',
				)}</td>
			</tr>`,
			)
			.join('');
		return this.format_table_block(
			'Top blocked SKUs',
			['SKU', 'Strategy', 'Min', 'Max', 'Purchase Cost', 'Violations'],
			body,
			'Amazon Pricing Push Readiness',
		);
	}

	format_below_min_table(below) {
		const body = below.rows
			.map(
				(row) => `
			<tr>
				<td>${frappe.utils.escape_html(row.proxy_sku || row.item_code || '')}</td>
				<td><a href="/app/sales-order/${encodeURIComponent(
					row.sales_order,
				)}">${frappe.utils.escape_html(row.sales_order || '')}</a></td>
				<td class="text-right">${flt(row.qty)}</td>
				<td class="text-right">${format_currency(row.rate || 0, 'GBP')}</td>
				<td class="text-right">${format_currency(row.min_price || 0, 'GBP')}</td>
				<td class="text-right text-danger">${format_currency(
					row.estimated_loss || 0,
					'GBP',
				)}</td>
			</tr>`,
			)
			.join('');
		return this.format_table_block(
			`Below-min sales (${below.days_back}d)`,
			['SKU', 'Sales Order', 'Qty', 'Sold At', 'Min', 'Est. Loss'],
			body,
			'Below Min Price Sales',
		);
	}

	format_feeds_table(rows) {
		const status_class = (status) =>
			status === 'Error'
				? 'text-danger'
				: status === 'Completed' || status === 'Submitted'
				  ? 'text-success'
				  : '';
		const body = rows
			.map(
				(row) => `
			<tr>
				<td>${frappe.utils.escape_html(row.submission_type || '')}</td>
				<td class="${status_class(row.status)}">${frappe.utils.escape_html(
					row.status || '',
				)}</td>
				<td>${frappe.utils.escape_html(row.amazon_status || '')}</td>
				<td class="text-right">${row.blocked_count || 0}</td>
				<td class="small text-muted">${frappe.datetime.str_to_user(
					row.creation,
				)}</td>
			</tr>`,
			)
			.join('');
		return this.format_table_block(
			'Recent feed submissions',
			['Type', 'Status', 'Amazon', 'Blocked', 'When'],
			body,
			'Amazon Pricing Push Monitor',
		);
	}

	format_fba_stock_table(fba_stock) {
		const rows = fba_stock?.rows || [];
		const body = rows
			.map(
				(row) => `
			<tr>
				<td><a href="/app/proxy-sku/${encodeURIComponent(
					row.proxy_sku,
				)}">${frappe.utils.escape_html(row.proxy_sku || '')}</a></td>
				<td>${frappe.utils.escape_html(
					row.item || '',
				)} · ${frappe.utils.escape_html(row.uom || '')}</td>
				<td class="small">${frappe.utils.escape_html(row.asin || '')}</td>
				<td class="text-right ${
					flt(row.afn_stock) <= 0 ? 'text-danger' : ''
				}">${flt(row.afn_stock)}</td>
				<td class="text-center">${row.in_stock ? '✓' : '—'}</td>
			</tr>`,
			)
			.join('');
		return this.format_table_block(
			'FBA stock (lowest fulfillable first)',
			['Proxy SKU', 'Item · UOM', 'ASIN', 'AFN Stock', 'In Stock'],
			body,
			'Amazon FBA Stock Sync Health',
		);
	}

	format_quick_actions(data) {
		const wrapper = $(
			'<div class="acc-section"><div class="acc-section-title">Quick actions</div><div class="acc-actions"></div></div>',
		);
		const actions_row = wrapper.find('.acc-actions');
		const add_button = (label, css_class, handler) => {
			const button = $(
				`<button class="btn btn-sm ${css_class}">${label}</button>`,
			);
			button.on('click', handler);
			actions_row.append(button);
		};

		const alerts = data.alerts || {};
		const alert_state = !alerts.telegram_enabled
			? 'Alerts OFF'
			: alerts.telegram_configured
			  ? 'Alerts ON'
			  : 'Alerts: chat not set';

		add_button('Take pricing snapshot', 'btn-primary', () =>
			this.capture_snapshot(),
		);
		add_button('Refresh strategies', 'btn-default', () =>
			this.run_action(
				'navgold_custom.navgold_custom.amazon_pricing.strategy_selector.bulk_refresh_strategies',
				{},
				'Re-select active strategy for all SKUs from current stock?',
			),
		);
		add_button('Fetch AFN stock', 'btn-default', () =>
			this.run_action(
				'navgold_custom.navgold_custom.amazon_pricing.afn_stock_fetcher.bulk_fetch_afn_stock',
				{},
				'Pull FBA fulfillable stock from Amazon into ERP now?',
			),
		);
		add_button('Send digest now', 'btn-default', () =>
			this.run_action(
				'navgold_custom.navgold_custom.amazon_pricing.alerts.send_digest',
				{},
				'Send the rollup digest to Telegram now?',
			),
		);
		add_button('Test alert', 'btn-default', () =>
			this.run_action(
				'navgold_custom.navgold_custom.amazon_pricing.alerts.send_test',
				{},
				'Send a Telegram test message?',
			),
		);

		actions_row.append(
			`<a class="btn btn-sm btn-default" href="/app/query-report/${encodeURIComponent(
				'Amazon Pricing Feed Preview',
			)}">Feed Preview</a>`,
		);
		actions_row.append(
			`<a class="btn btn-sm btn-default" href="/app/navgold-settings">Navgold Settings</a>`,
		);
		actions_row.append(
			`<a class="btn btn-sm btn-default" href="/app/amazon-sp-api-settings">SP-API Settings</a>`,
		);
		actions_row.append(
			`<a class="btn btn-sm btn-default" href="/app/amazon-pricing-control">Reports Workspace</a>`,
		);
		actions_row.append(
			`<span class="acc-chip neutral" style="align-self:center">${alert_state}</span>`,
		);
		return wrapper;
	}

	async capture_snapshot() {
		const confirmed = await navgold.confirm_dialog(
			'Capture a pricing snapshot of all active SKUs now?',
		);
		if (!confirmed) return;
		const { message } = await frappe.call({
			method:
				'navgold_custom.navgold_custom.doctype.amazon_pricing_snapshot.amazon_pricing_snapshot.capture_snapshot_now',
		});
		frappe.msgprint(`Snapshot ${message.name} captured`);
		frappe.set_route('Form', 'Amazon Pricing Snapshot', message.name);
	}

	async run_action(method, args, confirm_text) {
		if (confirm_text) {
			const confirmed = await navgold.confirm_dialog(confirm_text);
			if (!confirmed) return;
		}
		await frappe.call({ method, args });
		frappe.msgprint('Done');
		this.refresh(true);
	}

	format_nav_panel() {
		const report = (name) =>
			`<a class="acc-link" href="/app/query-report/${encodeURIComponent(
				name,
			)}">${name}</a>`;
		const doc = (label, route) =>
			`<a class="acc-link" href="/app/${route}">${label}</a>`;

		const groups = [
			[
				'Go-live readiness reports',
				[
					report('Amazon Go-Live Data Readiness'),
					report('Amazon Pricing Push Readiness'),
					report('Amazon Pricing Feed Preview'),
					report('Supplier Data Readiness'),
					report('Amazon Strategy Price Comparison'),
				],
			],
			[
				'Live monitoring reports',
				[
					report('Below Min Price Sales'),
					report('Amazon Pricing Push Monitor'),
					report('Amazon Order Sync Health'),
					report('Amazon FBA Stock Sync Health'),
				],
			],
			[
				'Pricing & cost reports',
				[
					report('Amazon Purchase Cost Overview'),
					report('Amazon Min Max Report'),
				],
			],
			[
				'Operational reports',
				[
					report('To Purchase'),
					report('Navgold Forecast Report'),
					report('Navgold Stock Summary'),
					report('FBA Transfer Tracker'),
				],
			],
			[
				'Key records',
				[
					doc('Amazon Product Pricing', 'amazon-product-pricing'),
					doc('Amazon Product Strategy', 'amazon-product-strategy'),
					doc('Proxy SKU', 'proxy-sku'),
					doc('Pricing Submission Log', 'amazon-pricing-submission-log'),
					doc('Failed Sync Records', 'amazon-failed-sync-record'),
					doc('Supplier Item Price and Stock', 'supplier-item-price-and-stock'),
				],
			],
		];

		const columns = groups
			.map(
				([title, links]) =>
					`<div class="acc-nav-col"><div class="acc-nav-title">${title}</div>${links.join(
						'',
					)}</div>`,
			)
			.join('');
		return $(`<div class="acc-section"><div class="acc-section-title">Jump to everything</div>
			<div class="acc-nav">${columns}</div></div>`);
	}

	format_table_block(title, headers, body_html, report_name) {
		const header_html = headers.map((header) => `<th>${header}</th>`).join('');
		const link_html = report_name
			? `<a class="acc-open" href="/app/query-report/${encodeURIComponent(
					report_name,
			  )}">Open full report →</a>`
			: '';
		return $(`
			<div class="acc-table-card">
				<div class="acc-table-head"><span>${title}</span>${link_html}</div>
				<div class="acc-table-scroll">
					<table class="table table-bordered acc-table">
						<thead><tr>${header_html}</tr></thead>
						<tbody>${
							body_html ||
							`<tr><td colspan="${headers.length}" class="text-muted text-center">None</td></tr>`
						}</tbody>
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
			.acc-status { border-radius:10px; padding:14px 16px; margin:10px 0; border:1px solid #e6e9ec; }
			.acc-status.safe { background:#f1f9f4; border-color:#bfe6d0; }
			.acc-status.warn { background:#fff7e8; border-color:#f3dca0; }
			.acc-status.frozen { background:#fdecec; border-color:#f3c9c7; }
			.acc-status-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:6px 28px; }
			@media (max-width:900px){ .acc-status-grid { grid-template-columns:1fr; } }
			.acc-status-col { display:grid; grid-template-columns:1fr auto; gap:0 14px; align-content:start; min-width:0; }
			.acc-status-title { grid-column:1 / -1; font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:.04em; color:#8a929b; margin:0 0 4px; }
			.acc-status-label { color:#55606a; font-size:13px; padding:4px 0; border-bottom:1px solid rgba(0,0,0,.05); }
			.acc-stat-val { font-size:13px; font-weight:700; text-align:right; padding:4px 0; border-bottom:1px solid rgba(0,0,0,.05); }
			.acc-stat-val.on, .acc-stat-val.safe { color:#176a3d; }
			.acc-stat-val.off { color:#6b7280; font-weight:600; }
			.acc-stat-val.bad { color:#b3261e; }
			.acc-stat-val.warn { color:#92560a; }
			.acc-stat-val.neutral { color:#3a4452; font-weight:600; }
			.acc-status-foot { margin-top:12px; font-size:11.5px; color:#8a929b; }
			.acc-chips { display:flex; flex-wrap:wrap; gap:8px; margin:10px 0; }
			.acc-chip { font-size:12px; padding:4px 10px; border-radius:8px; background:#eef0f2; border:1px solid #dfe3e6; }
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
			.acc-nav { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:16px; }
			.acc-nav-title { font-weight:600; font-size:12px; text-transform:uppercase; color:#8a929b; margin-bottom:6px; }
			.acc-link { display:block; padding:3px 0; font-size:13px; }
		`;
		$(`<style id="acc-styles">${css}</style>`).appendTo(document.head);
	}
}
