// async/await based confirm dialog
frappe.provide('navgold');

navgold.confirm_dialog = function confirm_dialog(message) {
	return new Promise((resolve) => {
		frappe.confirm(
			message,
			() => resolve(true),
			() => resolve(false),
		);
	});
};
