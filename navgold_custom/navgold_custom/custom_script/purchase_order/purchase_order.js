const SUPPLIER_PART_NUMBER_METHOD =
	'navgold_custom.navgold_custom.custom_script.purchase_order.purchase_order.get_supplier_part_number';

async function refresh_supplier_code(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (!row) return;

	if (!frm.doc.supplier || !row.item_code || !row.uom) {
		await frappe.model.set_value(cdt, cdn, 'supplier_part_no', '');
		return;
	}

	const response = await frappe.call({
		method: SUPPLIER_PART_NUMBER_METHOD,
		args: {
			supplier: frm.doc.supplier,
			item_code: row.item_code,
			uom: row.uom,
		},
	});

	await frappe.model.set_value(cdt, cdn, 'supplier_part_no', response.message || '');
}

async function refresh_all_supplier_codes(frm) {
	for (const row of frm.doc.items || []) {
		await refresh_supplier_code(frm, row.doctype, row.name);
	}
}

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
	async supplier(frm) {
		await refresh_all_supplier_codes(frm);
	},
});

frappe.ui.form.on('Purchase Order Item', {
	async item_code(frm, cdt, cdn) {
		await refresh_supplier_code(frm, cdt, cdn);
	},
	async uom(frm, cdt, cdn) {
		await refresh_supplier_code(frm, cdt, cdn);
	},
});
