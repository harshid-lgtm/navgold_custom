// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Navgold Settings', {
	// refresh(frm) {

	// },
	async generate_proxy_skus(frm) {
		frm.call({
			doc: frm.doc,
			method: 'generate_proxy_skus_for_items',
		});
		frappe.show_alert(
			{ message: __('Proxy SKUs generation started'), indicator: 'blue' },
			10,
		);
	},
});
