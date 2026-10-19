frappe.provide('navgold');

navgold.show_payment_banner = function show_payment_banner() {
	if (!frappe.boot || !frappe.boot.navgold_payment_locked) {
		return;
	}
	if (document.getElementById('navgold-payment-banner')) {
		return;
	}
	const banner = document.createElement('div');
	banner.id = 'navgold-payment-banner';
	banner.textContent = __(
		'Please make the payment, else the app will stop working.',
	);
	Object.assign(banner.style, {
		position: 'sticky',
		top: '0',
		zIndex: '1030',
		width: '100%',
		padding: '10px 16px',
		textAlign: 'center',
		fontWeight: '600',
		color: '#fff',
		background: 'var(--red-600, #c0392b)',
	});
	document.body.prepend(banner);
};

$(document).on('app_ready', navgold.show_payment_banner);
