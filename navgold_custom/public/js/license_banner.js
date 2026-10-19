frappe.provide('navgold');

navgold.fmt_time = function fmt_time(seconds) {
	const total = Math.max(0, Math.floor(seconds));
	const hours = Math.floor(total / 3600);
	const minutes = Math.floor((total % 3600) / 60);
	const secs = total % 60;
	const pad = (value) => String(value).padStart(2, '0');
	return hours > 0
		? `${hours}:${pad(minutes)}:${pad(secs)}`
		: `${pad(minutes)}:${pad(secs)}`;
};

navgold.sync_clock = function sync_clock() {
	navgold.clock_skew =
		Date.now() - Date.parse(frappe.boot.navgold_payment_server_now) || 0;
};

navgold.lockout_state = function lockout_state() {
	const boot = frappe.boot;
	const since = Date.parse(boot.navgold_payment_disable_since);
	const elapsed = (Date.now() - navgold.clock_skew - since) / 1000;
	const lock = cint(boot.navgold_payment_lock_sec);
	const open = cint(boot.navgold_payment_open_sec);
	if (open <= 0) {
		return { blocked: true, remaining: 0 };
	}
	const cycle = lock + open;
	const position = ((elapsed % cycle) + cycle) % cycle;
	if (position < lock) {
		return { blocked: true, remaining: Math.ceil(lock - position) };
	}
	return { blocked: false, remaining: Math.ceil(cycle - position) };
};

navgold.banner_info = function banner_info() {
	const boot = frappe.boot || {};
	if (boot.navgold_payment_disable && boot.navgold_payment_disable_since) {
		const state = navgold.lockout_state();
		if (state.blocked) {
			return {
				phase: 'locked',
				text: __('Operations are locked due to pending payment. Reopens in'),
				time: navgold.fmt_time(state.remaining),
			};
		}
		return {
			phase: 'grace',
			text: __('Please make the payment. Operations lock in'),
			time: navgold.fmt_time(state.remaining),
		};
	}
	if (boot.navgold_payment_warn) {
		return {
			phase: 'warn',
			text: __('Please make the payment, else the app will stop working.'),
			time: '',
		};
	}
	return null;
};

navgold.refresh_banner = function refresh_banner(frm) {
	if (!frm || !frm.dashboard || !frm.layout) {
		return;
	}
	const info = navgold.banner_info();
	const $banner = frm.layout.message.find('.navgold-payment-banner');
	if ($banner.length && info && $banner.data('phase') === info.phase) {
		$banner.find('.navgold-countdown').text(info.time);
		return;
	}
	$banner.closest('.form-message').remove();
	if (!info) {
		if (!frm.layout.message.children().length) {
			frm.layout.message.addClass('hidden');
		}
		return;
	}
	const time_html = info.time
		? ` <b class="navgold-countdown">${info.time}</b>`
		: '';
	frm.dashboard.set_headline(
		`<div class="navgold-payment-banner" data-phase="${info.phase}">${info.text}${time_html}</div>`,
		'red',
		true,
	);
};

navgold.start_banner_ticker = function start_banner_ticker() {
	if (navgold.banner_timer) {
		return;
	}
	navgold.banner_timer = setInterval(
		() => navgold.refresh_banner(window.cur_frm),
		1000,
	);
};

navgold.show_payment_nag = function show_payment_nag() {
	if (!frappe.boot || !frappe.boot.navgold_payment_warn) {
		return;
	}
	if (navgold.nag_open || document.querySelector('.modal.show')) {
		return;
	}
	navgold.nag_open = true;
	navgold.show_nag_chain(cint(frappe.boot.navgold_payment_nag_count));
};

navgold.show_nag_chain = function show_nag_chain(remaining) {
	if (remaining <= 0 || !frappe.boot.navgold_payment_warn) {
		navgold.nag_open = false;
		return;
	}
	const dialog = new frappe.ui.Dialog({
		title: __('Payment Required'),
		indicator: 'red',
		primary_action_label: __('OK'),
		primary_action() {
			dialog.hide();
		},
	});
	dialog.$body.html(
		`<p>${__('Please make the payment, else the app will stop working.')}</p>`,
	);
	dialog.onhide = () =>
		setTimeout(() => navgold.show_nag_chain(remaining - 1), 300);
	dialog.show();
};

navgold.start_payment_nag = function start_payment_nag() {
	const interval_sec = cint(frappe.boot.navgold_payment_warn_interval) || 120;
	if (navgold.nag_timer && navgold.nag_interval_sec === interval_sec) {
		return;
	}
	clearInterval(navgold.nag_timer);
	navgold.nag_interval_sec = interval_sec;
	navgold.nag_timer = setInterval(
		navgold.show_payment_nag,
		interval_sec * 1000,
	);
};

navgold.start_dev_poll = function start_dev_poll() {
	if (!frappe.boot.navgold_payment_dev_poll || navgold.dev_poll_timer) {
		return;
	}
	navgold.dev_poll_timer = setInterval(async () => {
		const state = await frappe.xcall(
			'navgold_custom.navgold_custom.license_gate.refresh_now',
		);
		Object.assign(frappe.boot, state);
		navgold.sync_clock();
		navgold.start_payment_nag();
	}, 1000);
};

if (!window.Cypress) {
	frappe.ui.form.on('*', {
		refresh: navgold.refresh_banner,
	});

	$(document).on('app_ready', () => {
		navgold.sync_clock();
		navgold.show_payment_nag();
		navgold.start_payment_nag();
		navgold.start_banner_ticker();
		navgold.start_dev_poll();
	});
}
