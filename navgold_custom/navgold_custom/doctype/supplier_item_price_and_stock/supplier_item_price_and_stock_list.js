frappe.listview_settings['Supplier Item Price and Stock'] = {
	onload(listview) {
		// prevent duplicate buttons
		if (listview.page._refresh_ranks_btn) return;

		listview.page.add_inner_button(__('Update Rank'), async () => {
			frappe.call({
				method:
					'navgold_custom.navgold_custom.doctype.supplier_item_price_and_stock.supplier_item_price_and_stock.update_supplier_rank',
			});
			frappe.toast(__('Updating Supplier ranks in background.'));
		});

		listview.page._refresh_ranks_btn = true;
	},
};
