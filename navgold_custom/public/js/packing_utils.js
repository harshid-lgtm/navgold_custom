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

navgold.packing.attach_camera_scanner = function attach_camera_scanner(
	on_scan,
	{ show_scanner = true } = {},
) {
	if (!show_scanner) return null;
	return new frappe.ui.Scanner({ dialog: true, on_scan });
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
		navgold.packing.attach_camera_scanner(
			(data) => {
				dialog.fields_dict.tote_barcode.$input.val(data.decodedText);
				dialog.fields_dict.tote_barcode.$input.trigger('change');
			},
			{ show_scanner },
		);
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
		navgold.packing.attach_camera_scanner(
			(data) => {
				dialog.fields_dict.item_barcode.$input.val(data.decodedText);
				dialog.fields_dict.item_barcode.$input.trigger('change');
			},
			{ show_scanner },
		);
	}, 150);
};

navgold.packing.download_file = function download_file(file_url, file_name) {
	const link = document.createElement('a');
	link.href = file_url;
	link.download = file_name || '';
	document.body.appendChild(link);
	link.click();
	document.body.removeChild(link);
};

navgold.packing.prompt_select = function prompt_select({
	title,
	label,
	options,
	on_select,
}) {
	const dialog = new frappe.ui.Dialog({
		title,
		fields: [
			{
				fieldname: 'value',
				fieldtype: 'Select',
				label: label || __('Select'),
				options: options.join('\n'),
				reqd: 1,
				default: options[0],
			},
		],
		primary_action_label: __('Select'),
		async primary_action({ value }) {
			dialog.hide();
			await on_select(value);
		},
	});
	dialog.show();
};
