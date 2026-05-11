// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt
//
// Shared utilities for Packing Slip and FBA Packing Slip scan workflows.

frappe.provide('navgold.packing');

navgold.packing.resolve_barcode = async function resolve_barcode(barcode) {
	const response = await frappe.call({
		method:
			'navgold_custom.navgold_custom.custom_script.packing_slip.packing_utils.resolve_barcode',
		args: { search_value: barcode },
	});
	return response.message;
};

navgold.packing.open_scan_tote_dialog = function open_scan_tote_dialog(
	frm,
	tote_fieldname,
	show_scanner = true,
) {
	const dialog = new frappe.ui.Dialog({
		title: __('Scan Tote'),
		fields: [
			{
				fieldname: 'tote_barcode',
				fieldtype: 'Data',
				label: __('Tote Barcode'),
				options: 'Barcode',
				reqd: 1,
			},
		],
		primary_action_label: __('Set Tote'),
		async primary_action({ tote_barcode }) {
			await frm.set_value(tote_fieldname, tote_barcode);
			dialog.hide();
			frappe.show_alert({
				message: __('Tote set to {0}', [tote_barcode]),
				indicator: 'green',
			});
			frm.refresh();
		},
	});
	dialog.show();
	setTimeout(() => {
		dialog.fields_dict.tote_barcode.$input.focus();
		if (show_scanner) {
			new frappe.ui.Scanner({
				dialog: true,
				on_scan: (data) => {
					dialog.fields_dict.tote_barcode.$input.val(data.decodedText);
					dialog.fields_dict.tote_barcode.$input.trigger('change');
				},
			});
		}
	}, 150);
};

navgold.packing.open_scan_item_dialog = function open_scan_item_dialog(
	frm,
	process_scan,
	show_scanner = true,
) {
	const dialog = new frappe.ui.Dialog({
		title: __('Scan Item Barcode'),
		fields: [
			{
				fieldname: 'item_barcode',
				fieldtype: 'Data',
				label: __('Item Barcode'),
				options: 'Barcode',
				reqd: 1,
			},
		],
		primary_action_label: __('Scan'),
		async primary_action({ item_barcode }) {
			dialog.hide();
			await process_scan(frm, item_barcode);
		},
	});
	dialog.show();
	setTimeout(() => {
		dialog.fields_dict.item_barcode.$input.focus();
		if (show_scanner) {
			new frappe.ui.Scanner({
				dialog: true,
				on_scan: (data) => {
					dialog.fields_dict.item_barcode.$input.val(data.decodedText);
					dialog.fields_dict.item_barcode.$input.trigger('change');
				},
			});
		}
	}, 150);
};
