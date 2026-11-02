frappe.listview_settings['Supplier Stock Import'] = {
	onload(listview) {
		if (listview.page._clf_fetch_buttons) return;

		const start_fetch = async (method, label) => {
			await frappe.call({
				method: `navgold_custom.navgold_custom.doctype.supplier_stock_import.clf_stock_api.${method}`,
			});
			frappe.show_alert({
				message: __('Fetching CLF {0} in the background...', [label]),
				indicator: 'blue',
			});
		};

		listview.page.add_inner_button(
			__('CLF Stock'),
			() => start_fetch('fetch_clf_stock', __('stock')),
			__('Fetch'),
		);
		listview.page.add_inner_button(
			__('CLF Price'),
			() => start_fetch('fetch_clf_price', __('price')),
			__('Fetch'),
		);

		listview.page.add_inner_button(
			__('Suma Stock'),
			() => {
				const dialog = new frappe.ui.Dialog({
					title: __('Import Suma Stock'),
					fields: [
						{
							fieldname: 'file_url',
							fieldtype: 'Attach',
							label: __('Raw Suma Excel File'),
							reqd: 1,
						},
					],
					primary_action_label: __('Import Stock'),
					primary_action: async (values) => {
						const response = await frappe.call({
							method:
								'navgold_custom.navgold_custom.doctype.supplier_stock_import.suma_stock_import.start_suma_stock_import',
							args: { file_url: values.file_url },
						});
						dialog.hide();
						frappe.show_alert({
							message: __('Suma stock import started: {0}', [
								response.message.docname,
							]),
							indicator: 'blue',
						});
					},
				});
				dialog.show();
			},
			__('Import'),
		);

		frappe.realtime.off('suma_stock_imported');
		frappe.realtime.on('suma_stock_imported', ({ docname, error }) => {
			if (error) {
				frappe.msgprint({
					title: __('Suma Stock Import'),
					message: error,
					indicator: 'red',
				});
				return;
			}
			frappe.show_alert({
				message: __('Suma stock imported: {0}', [docname]),
				indicator: 'green',
			});
			listview.refresh();
		});

		frappe.realtime.off('clf_fetched');
		frappe.realtime.on('clf_fetched', ({ metric, docname, error }) => {
			if (error) {
				frappe.msgprint({
					title: __('CLF {0} Fetch', [metric]),
					message: error,
					indicator: 'red',
				});
				return;
			}
			frappe.show_alert({
				message: __('CLF {0} fetched: {1}', [metric, docname]),
				indicator: 'green',
			});
			listview.refresh();
		});

		listview.page._clf_fetch_buttons = true;
	},
};
