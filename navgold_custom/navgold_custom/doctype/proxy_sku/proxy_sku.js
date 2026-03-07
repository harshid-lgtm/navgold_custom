// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Proxy SKU', {
	refresh(frm) {
		if (!frm.doc.is_active) return;
		frm.add_custom_button(__('Create Next Proxy SKU'), async () => {
			const response = await frappe.call({
				doc: frm.doc,
				method: 'create_next_proxy_sku',
			});
			frappe.set_route('Form', 'Proxy SKU', response.message.name);
			frappe.msgprint(__('Next Proxy SKU Created'));
		});

		if (!frm.doc.asin) {
			frm.add_custom_button(__('Fetch ASIN'), async () => {
				const response = await frappe.call({
					doc: frm.doc,
					method: 'fetch_asin',
					freeze: true,
					freeze_message: __('Fetching ASIN from Amazon...'),
				});
				if (response.message) {
					frm.reload_doc();
					frappe.show_alert({
						message: __('ASIN fetched: {0}', [response.message]),
						indicator: 'green',
					});
				}
			});
		}

		if (frm.doc.is_fba && !frm.doc.fnsku) {
			frm.add_custom_button(__('Fetch FNSKU'), async () => {
				const response = await frappe.call({
					doc: frm.doc,
					method: 'fetch_fnsku',
					freeze: true,
					freeze_message: __('Fetching FNSKU from Amazon...'),
				});
				if (response.message) {
					frm.reload_doc();
					frappe.show_alert({
						message: __('FNSKU fetched: {0}', [response.message]),
						indicator: 'green',
					});
				}
			});
		}
	},
});
