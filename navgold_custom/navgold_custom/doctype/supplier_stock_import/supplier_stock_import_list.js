frappe.listview_settings['Supplier Stock Import'] = {
	onload(listview) {
		// prevent duplicate buttons
		if (listview.page._fetch_clf_stock_btn) return;

		listview.page.add_inner_button(__('Fetch CLF Stock'), async () => {
			await frappe.call({
				method:
					'navgold_custom.navgold_custom.doctype.supplier_stock_import.supplier_stock_import.fetch_clf_stock',
			});
			frappe.show_alert({
				message: __('Fetching CLF stock in the background...'),
				indicator: 'blue',
			});
		});

		frappe.realtime.off('clf_stock_fetched');
		frappe.realtime.on('clf_stock_fetched', ({ docname, error }) => {
			if (error) {
				frappe.msgprint({
					title: __('CLF Stock Fetch'),
					message: error,
					indicator: 'red',
				});
				return;
			}
			frappe.show_alert({
				message: __('CLF stock fetched: {0}', [docname]),
				indicator: 'green',
			});
			listview.refresh();
		});

		listview.page._fetch_clf_stock_btn = true;
	},
};
