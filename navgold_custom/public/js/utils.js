function confirm_dialog(message) {
	return new Promise((resolve) => {
		frappe.confirm(
			message,
			() => resolve(true),
			() => resolve(false),
		);
	});
}
