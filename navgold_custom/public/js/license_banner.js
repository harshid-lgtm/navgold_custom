frappe.provide('navgold');

navgold.add_payment_banner = function add_payment_banner(frm) {
	if (!frappe.boot || !frappe.boot.navgold_payment_warn) {
		return;
	}
	frm.dashboard.add_comment(
		__('Please make the payment, else the app will stop working.'),
		'red',
		true,
	);
};

navgold.show_payment_nag = function show_payment_nag() {
	if (!frappe.boot || !frappe.boot.navgold_payment_warn) {
		return;
	}
	if (document.querySelector('.modal.show')) {
		return;
	}
	navgold.show_nag_chain(cint(frappe.boot.navgold_payment_nag_count));
};

navgold.show_nag_chain = function show_nag_chain(remaining) {
	if (remaining <= 0) {
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
	navgold.show_payment_nag();
	if (navgold.nag_timer) {
		return;
	}
	const interval_ms =
		(cint(frappe.boot.navgold_payment_warn_interval) || 120) * 1000;
	navgold.nag_timer = setInterval(navgold.show_payment_nag, interval_ms);
};

if (!window.Cypress) {
	frappe.ui.form.on('*', {
		refresh: navgold.add_payment_banner,
	});

	$(document).on('app_ready', navgold.start_payment_nag);
}
