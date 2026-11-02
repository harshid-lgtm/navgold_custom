frappe.ui.form.on('Temu API Settings', {
	refresh(frm) {
		frm.set_query('warehouse', () => ({
			filters: {
				is_group: 0,
				company: frm.doc.company,
			},
		}));
	},

	get_order(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __('Fetch Temu Order'),
			fields: [
				{
					fieldname: 'parent_order_sn',
					fieldtype: 'Data',
					label: __('Temu Order ID'),
					reqd: 1,
				},
			],
			primary_action_label: __('Get Order'),
			primary_action: async (values) => {
				const response = await frm.call('fetch_order', {
					parent_order_sn: values.parent_order_sn,
				});
				dialog.hide();
				await frm.reload_doc();

				const order_count = response.message?.result?.pageItems?.length || 0;
				frappe.msgprint({
					title: __('Temu Order Fetch'),
					message: order_count
						? __('Temu returned {0} matching order result(s). Review Last Order Response below.', [
							order_count,
						])
						: __('Temu returned no matching orders for that Order ID.'),
					indicator: order_count ? 'green' : 'orange',
				});
			},
		});

		dialog.show();
	},
});
