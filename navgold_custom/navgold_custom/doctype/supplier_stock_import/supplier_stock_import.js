// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Supplier Stock Import', {
	setup(frm) {
		frm.get_field('entry_list').df.options = {
			restrictions: {
				allowed_file_types: ['.csv', '.xlsx', '.xls'],
			},
		};

		frappe.realtime.on('supplier_import_refresh', ({ docname }) => {
			if (docname !== frm.doc.name) return;
			frappe.model.clear_doc('Supplier Stock Import', frm.doc.name);
			frappe.model.with_doc('Supplier Stock Import', frm.doc.name).then(() => {
				frm.reload_doc();
			});
		});

		frappe.realtime.on('supplier_import_progress', (data) => {
			if (data.docname !== frm.doc.name) return;
			const percent = Math.floor((data.current * 100) / data.total);
			const message = __('Importing {0} of {1}', [data.current, data.total]);
			frm.dashboard.show_progress(__('Import Progress'), percent, message);
			frm.page.set_indicator(__('In Progress'), 'orange');

			if (data.current === data.total) {
				setTimeout(() => {
					frm.dashboard.hide();
				}, 2000);
			}
		});
	},

	onload(frm) {
		if (frm.is_new() && !frm.doc.currency) {
			frm.set_value('currency', frappe.defaults.get_global_default('currency'));
		}
	},

	refresh(frm) {
		frm.trigger('update_status_indicator');

		const has_import_source = frm.doc.entry_list || frm.doc.google_sheets_url;
		if (!frm.is_new() && frm.doc.status === 'Pending' && has_import_source) {
			frm.add_custom_button(
				__('Start Import'),
				() => {
					frm.trigger('start_import');
				},
				null,
				'primary',
			);
		}
		if (
			!frm.is_new() &&
			frm.doc.status !== 'Pending' &&
			frm.doc.status !== 'In Progress'
		) {
			frm.add_custom_button(__('Re-Import'), () => {
				frm.trigger('reset_and_reimport');
			});
		}

		if (frm.doc.status === 'Success' || frm.doc.status === 'Partial Success') {
			frm.add_custom_button(__('View Stock Records'), () => {
				frappe.set_route('List', 'Supplier Item Price and Stock', {
					supplier: frm.doc.supplier,
				});
			});
		}
		if (frm.doc.status === 'Partial Success' || frm.doc.status === 'Error') {
			if (frm.doc.failed_imports > 0) {
				frm.add_custom_button(__('Export Errored Rows'), () => {
					frm.trigger('export_errored_rows');
				});
			}
		}
	},

	download_template() {
		const method =
			'/api/method/frappe.core.doctype.data_import.data_import.download_template';

		open_url_post(method, {
			doctype: 'Supplier Item Price and Stock',
			export_records: 'blank_template',
			export_fields: {
				'Supplier Item Price and Stock': [
					'supplier_part_number',
					'stock_qty',
					'uom',
					'price',
					'barcode',
				],
			},
		});
	},

	supplier(frm) {
		if (frm.doc.status !== 'Pending') {
			frm.set_value('status', 'Pending');
			frm.set_value('import_log', '');
			frm.set_value('total_rows', 0);
			frm.set_value('successful_imports', 0);
			frm.set_value('failed_imports', 0);
		}
	},

	entry_list(frm) {
		if (frm.doc.entry_list && frm.doc.status !== 'Pending') {
			frm.set_value('status', 'Pending');
			frm.set_value('import_log', '');
			frm.set_value('total_rows', 0);
			frm.set_value('successful_imports', 0);
			frm.set_value('failed_imports', 0);
		}
	},

	google_sheets_url(frm) {
		if (frm.doc.google_sheets_url && frm.doc.status !== 'Pending') {
			frm.set_value('status', 'Pending');
			frm.set_value('import_log', '');
			frm.set_value('total_rows', 0);
			frm.set_value('successful_imports', 0);
			frm.set_value('failed_imports', 0);
		}
	},

	update_status_indicator(frm) {
		const status_colors = {
			Pending: 'orange',
			'In Progress': 'blue',
			Success: 'green',
			'Partial Success': 'yellow',
			Error: 'red',
		};

		if (frm.doc.status && !frm.is_new()) {
			frm.page.set_indicator(
				__(frm.doc.status),
				status_colors[frm.doc.status] || 'gray',
			);
		}
	},

	reset_and_reimport(frm) {
		frappe.confirm(
			__(
				'This will re-process the file and may update existing records. Continue?',
			),
			() => {
				frm.set_value('status', 'Pending');
				frm.set_value('import_log', '');
				frm.set_value('total_rows', 0);
				frm.set_value('successful_imports', 0);
				frm.set_value('failed_imports', 0);
				frm.save();
			},
		);
	},

	export_errored_rows(frm) {
		const method =
			'/api/method/navgold_custom.navgold_custom.doctype.supplier_stock_import.supplier_stock_import.export_errored_rows';
		open_url_post(method, { docname: frm.doc.name });
	},

	start_import(frm) {
		frm.call('start_import');
		frappe.show_alert({
			message: __('Import started in background'),
			indicator: 'green',
		});
	},
});
