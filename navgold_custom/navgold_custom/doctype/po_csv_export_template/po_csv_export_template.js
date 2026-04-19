// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('PO CSV Export Template', {
	refresh(frm) {
		render_mapping_preview(frm);
	},
	template_file(frm) {
		render_mapping_preview(frm);
	},
});

function render_mapping_preview(frm) {
	const wrapper = frm.get_field('mapping_preview').$wrapper.empty();
	if (frm.is_new() || !frm.doc.template_file) return;

	frappe.require('data_import_tools.bundle.js', () => {
		frm.call('get_field_mapping_preview').then((r) => {
			const preview_data = r.message;
			if (!preview_data || !preview_data.columns.length) {
				wrapper.html(
					`<p class="text-muted">${__(
						'Upload a template file to see the field mapping.',
					)}</p>`,
				);
				return;
			}
			const preview = new frappe.data_import.ImportPreview({
				wrapper,
				doctype: 'Purchase Order Item',
				preview_data,
				frm: { doc: { status: 'Success' } },
				import_log: [],
			});
			preview.show_column_warning = () => {};
		});
	});
}
