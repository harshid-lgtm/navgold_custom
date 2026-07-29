frappe.ui.form.on('Sales Order', {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.amazon_order_id) {
			return;
		}

		frm.add_custom_button(
			__('Refresh Amazon Status'),
			async () => {
				const confirmed = await navgold.confirm_dialog(
					__(
						'Fetch {0} from Amazon and apply its current status? If payment has cleared, this order will be priced and submitted.',
						[frm.doc.amazon_order_id],
					),
				);
				if (!confirmed) {
					return;
				}

				frappe.dom.freeze(__('Asking Amazon...'));
				try {
					const response = await frappe.call({
						method:
							'navgold_custom.navgold_custom.amazon_repository.refresh_sales_order_status',
						args: { sales_order: frm.doc.name },
					});
					frappe.show_alert({
						message: __('Amazon reports {0}', [
							response.message.amazon_order_status,
						]),
						indicator: 'green',
					});
				} finally {
					frappe.dom.unfreeze();
				}

				frm.reload_doc();
			},
			__('Amazon'),
		);
	},
});
