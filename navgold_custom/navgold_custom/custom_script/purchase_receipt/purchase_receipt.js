// Copyright (c) 2025
// Navgold - MIT License

frappe.ui.form.on('Purchase Receipt', {
	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__('Scan Item'), () =>
				frm.events.open_scan_dialog(frm),
			);
		}

		if (!frm._scan_helper_initialized) {
			frm._scan_helper_initialized = true;
			frm._scan_buffer = '';
			frm._scan_timer = null;
		}
	},

	open_scan_dialog(frm) {
		if (!frm.doc.supplier) {
			frappe.throw({
				title: __('Mandatory'),
				message: __('Please select a Supplier before scanning items'),
				indicator: 'red',
			});
		}

		const dialog = new frappe.ui.Dialog({
			title: __('Scan Barcode'),
			fields: [
				{ fieldtype: 'HTML', fieldname: 'scan_wrapper' },
				{
					fieldname: 'item_code_display',
					label: __('Item Code'),
					fieldtype: 'Read Only',
					hidden: 1,
				},
				{
					fieldname: 'item_name_display',
					label: __('Item Name'),
					fieldtype: 'Read Only',
					hidden: 1,
				},
			],
			primary_action_label: __('Close'),
			primary_action() {
				dialog.hide();
			},
		});

		dialog.show();

		const parent = dialog.fields_dict.scan_wrapper.$wrapper;
		const control = frappe.ui.form.make_control({
			parent: parent,
			df: {
				fieldtype: 'Data',
				fieldname: 'scan_barcode_control',
				label: __('Scan Barcode'),
				options: 'Barcode',
				reqd: 1,
				description: __('Scan or tap camera icon'),
			},
			render_input: true,
		});

		control.refresh();
		dialog.barcode_control = control;

		new frappe.ui.Scanner({
			dialog: true,
			on_scan: (data) => {
				control.$input.val(data.decodedText);
				control.$input.focus();
			},
		});

		const handle_scanner_value = async (barcode_value) => {
			if (frm._scan_locked) return;
			frm._scan_locked = true;

			const scanned = (barcode_value || '').trim();
			if (!scanned) {
				control.$input.focus();
				frm._scan_locked = false;
				return;
			}

			control.$input.prop('disabled', true);

			try {
				await frm.events.process_scanned_barcode(frm, scanned, dialog);
			} finally {
				control.$input.prop('disabled', false);
				control.set_input('');
				control.$input.focus();
				frm._scan_locked = false;
			}
		};

		control.$input.on('change', function () {
			handle_scanner_value($(this).val());
		});

		control.$input.on('keypress', function (e) {
			if (e.which === 13) {
				e.preventDefault();
				handle_scanner_value($(this).val());
			}
		});

		const onKeydown = (e) => {
			if (e.ctrlKey || e.altKey || e.metaKey) return;

			if (e.key.length === 1) {
				frm._scan_buffer += e.key;
			}

			if (frm._scan_timer) clearTimeout(frm._scan_timer);
			frm._scan_timer = setTimeout(() => {}, 60);

			if (e.key === 'Enter') {
				e.preventDefault();
				const visible = control.$input.val() || '';
				const value = visible.trim() || frm._scan_buffer.trim();
				frm._scan_buffer = '';
				handle_scanner_value(value);
			}
		};

		document.addEventListener('keydown', onKeydown);

		dialog.onhide = () => {
			document.removeEventListener('keydown', onKeydown);
			if (frm._scan_timer) clearTimeout(frm._scan_timer);
			frm._scan_buffer = '';
			control.$input.off('change');
			control.$input.off('keypress');
		};
	},

	async process_scanned_barcode(frm, barcode_value, dialog) {
		try {
			const r = await frappe.call({
				method: 'erpnext.stock.utils.scan_barcode',
				args: { search_value: barcode_value },
			});

			if (!r.message || !r.message.item_code) {
				frappe.msgprint(__('Barcode {0} not found', [barcode_value]));
				return;
			}

			const item_code = r.message.item_code;

			await frm.events.fetch_and_show_pos(
				frm,
				item_code,
				r.message,
				dialog,
				barcode_value,
			);
		} catch (err) {
			frappe.msgprint(__('Error scanning barcode'));
		}
	},

	async fetch_and_show_pos(
		frm,
		item_code,
		barcode_data,
		dialog,
		barcode_value,
	) {
		try {
			const r = await frappe.call({
				method:
					'navgold_custom.navgold_custom.custom_script.purchase_receipt.purchase_receipt.get_open_pos_for_item',
				args: {
					supplier: frm.doc.supplier,
					item_code,
				},
			});

			if (!r.message || r.message.length === 0) {
				frappe.msgprint(__('No open Purchase Orders for {0}', [item_code]));
				return;
			}

			await frm.events.show_po_selection_popup(
				frm,
				item_code,
				barcode_data,
				r.message,
				dialog,
				barcode_value,
			);
		} catch (err) {
			frappe.msgprint(__('Error fetching Purchase Orders'));
		}
	},

	show_po_selection_popup(
		frm,
		item_code,
		barcode_data,
		pos_list,
		scan_dialog,
		barcode_value,
	) {
		const po_options = pos_list
			.map(
				(po) =>
					`${po.po_name} - ${po.supplier_name} (Pending: ${flt(
						po.pending_qty,
					)} ${po.uom})`,
			)
			.join('\n');

		const default_warehouse =
			frm.doc.set_warehouse ||
			(frm.doc.items?.length ? frm.doc.items[0].warehouse : null);
		return new Promise((resolve) => {
			const d = new frappe.ui.Dialog({
				title: __('Select Purchase Order'),
				fields: [
					{
						label: __('Item Code'),
						fieldname: 'item_code_display',
						fieldtype: 'Read Only',
						default: item_code,
					},
					{
						label: __('Item Name'),
						fieldname: 'item_name_display',
						fieldtype: 'Read Only',
						default: barcode_data.item_name || '-',
					},
					{
						label: __('Barcode'),
						fieldname: 'barcode_display',
						fieldtype: 'Read Only',
						default: barcode_value,
					},
					{ fieldtype: 'Column Break' },
					{
						label: __('Purchase Order'),
						fieldname: 'po_selection',
						fieldtype: 'Select',
						options: po_options,
						reqd: 1,
					},
					{
						label: __('Batch No'),
						fieldname: 'batch_no',
						fieldtype: 'Data',
						reqd: 1,
					},
					{
						label: __('Expiry Date'),
						fieldname: 'expiry_date',
						fieldtype: 'Date',
						reqd: 1,
					},
					{ fieldtype: 'Section Break' },
					{
						label: __('Quantity'),
						fieldname: 'qty_input',
						fieldtype: 'Float',
						default: 1,
						reqd: 1,
					},
					{
						label: __('Warehouse'),
						fieldname: 'warehouse',
						fieldtype: 'Link',
						options: 'Warehouse',
						default: default_warehouse,
						reqd: 1,
						get_query() {
							// Todo: Add filters for organic warehouse
							const filters = {
								company: frm.doc.company,
								is_group: 0,
							};

							return { filters };
						},
					},
				],
				primary_action_label: __('Add Item'),

				primary_action: async (values) => {
					const po_data = pos_list.find(
						(po) =>
							`${po.po_name} - ${po.supplier_name} (Pending: ${flt(
								po.pending_qty,
							)} ${po.uom})` === values.po_selection,
					);

					if (!po_data) {
						frappe.msgprint(__('Invalid PO selection'));
						return;
					}

					if (values.qty_input > po_data.pending_qty) {
						frappe.msgprint(__('Quantity exceeds pending qty'));
						return;
					}
					const min_expiry_in_days = await frappe.db.get_single_value(
						'Navgold Settings',
						'min_shelf_expiry_in_days',
					);
					const current_expiry_in_days = frappe.datetime.get_diff(
						values.expiry_date,
						frappe.datetime.get_today(),
					);
					if (current_expiry_in_days < min_expiry_in_days) {
						frappe.confirm(
							__(
								'Selected date {0} is less than minimum shelf expiry ({1} days). Do you want to proceed?',
								[values.expiry_date, min_expiry_in_days],
							),
							async () => {
								await frm.events.map_po_item_to_pr(
									frm,
									po_data.po_name,
									po_data.po_item_name,
									values.qty_input,
									values.warehouse,
									values.batch_no,
									values.expiry_date,
									d,
									scan_dialog,
								);
							},
						);
					} else {
						await frm.events.map_po_item_to_pr(
							frm,
							po_data.po_name,
							po_data.po_item_name,
							values.qty_input,
							values.warehouse,
							values.batch_no,
							values.expiry_date,
							d,
							scan_dialog,
						);
					}

					resolve();
				},
			});

			d.show();
		});
	},

	async map_po_item_to_pr(
		frm,
		po_name,
		po_item_name,
		qty,
		warehouse,
		batch_no,
		expiry_date,
		po_dialog,
		scan_dialog,
	) {
		try {
			const r = await frappe.call({
				method:
					'navgold_custom.navgold_custom.custom_script.purchase_receipt.purchase_receipt.add_scanned_po_item_to_pr',
				args: {
					po_name,
					po_item_name,
					qty,
					warehouse,
					batch_no,
					expiry_date,
					target_pr_doc: JSON.stringify(frm.doc),
				},
				freeze: true,
				freeze_message: __('Adding item from Purchase Order...'),
			});

			if (r.message) {
				frappe.model.sync(r.message);
				frm.refresh_field('items');

				po_dialog.hide();

				frappe.show_alert({
					message: __('Item added with quantity {0}', [qty]),
					indicator: 'green',
				});

				frm.save();

				setTimeout(() => {
					if (frappe.ui.open_dialogs.length < 1) {
						frm.events.open_scan_dialog(frm);
					} else {
						new frappe.ui.Scanner({
							dialog: true,
							on_scan: (data) => {
								scan_dialog?.fields_dict.scan_wrapper.$wrapper
									.find('input')
									.val(data.decodedText);
								scan_dialog?.fields_dict.scan_wrapper.$wrapper
									.find('input')
									.focus();
							},
						});
					}
				}, 400);
			}
		} catch (err) {
			frappe.msgprint(__('Failed to add item'));
		}
	},
});
