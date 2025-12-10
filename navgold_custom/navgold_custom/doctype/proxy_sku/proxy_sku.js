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
			window.location.href = `/desk/proxy-sku/${response.message.name}`;
		});
	},
});
