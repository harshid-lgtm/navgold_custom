frappe.ui.form.on('Purchase Order', {
	refresh(frm) {
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__('Export Items To CSV'), () => {
				window.open(
					`/api/method/navgold_custom.navgold_custom.custom_script.purchase_order.purchase_order.export_po_items_summary?purchase_order=${encodeURIComponent(
						frm.doc.name,
					)}`,
				);
			});
		}
	},
});
