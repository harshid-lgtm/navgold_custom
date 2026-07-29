frappe.ui.form.on('Item', {
	async refresh(frm) {
		if (frm.is_new()) return;

		const proxy_skus = await frappe.db.get_list('Proxy SKU', {
			filters: { item: frm.doc.name },
			pluck: 'name',
			limit: 0,
		});
		if (!proxy_skus.length) return;

		frm.add_custom_button(
			__('Proxy SKU'),
			() => frappe.set_route('List', 'Proxy SKU', { item: frm.doc.name }),
			__('Amazon Linked'),
		);

		frm.add_custom_button(
			__('Amazon Product Pricing'),
			() =>
				frappe.set_route('List', 'Amazon Product Pricing', {
					proxy_sku: ['in', proxy_skus],
				}),
			__('Amazon Linked'),
		);

		frm.add_custom_button(
			__('Amazon Product Strategy'),
			() =>
				frappe.set_route('List', 'Amazon Product Strategy', {
					proxy_sku: ['in', proxy_skus],
				}),
			__('Amazon Linked'),
		);
	},
});
