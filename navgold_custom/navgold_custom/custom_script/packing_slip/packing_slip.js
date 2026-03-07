// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Packing Slip', {
	refresh(frm) {
		toggle_fields(frm);
		add_scan_buttons(frm);
	},
});

function toggle_fields(frm) {
	frm.toggle_display('custom_scan_item', false);
	frm.toggle_display('custom_item', false);
}

function add_scan_buttons(frm) {
	if (frm.doc.docstatus !== 0) return;

	if (!frm.doc.custom_tote) {
		frm.add_custom_button(__('Scan Tote'), () =>
			navgold.packing.open_scan_tote_dialog(frm, 'custom_tote'),
		);
	}

	if (frm.doc.custom_tote) {
		frm.add_custom_button(__('Scan Item Barcode'), () =>
			navgold.packing.open_scan_item_dialog(frm, process_item_scan),
		);
	}
}

async function process_item_scan(frm, barcode) {
	try {
		const scan_result = await navgold.packing.resolve_barcode(barcode);

		if (scan_result?.error) {
			frappe.show_alert({ message: scan_result.error, indicator: 'red' }, 5);
			return;
		}

		const item_code = scan_result.item_code;

		if (frm.selected_delivery_note) {
			show_packed_qty_dialog(frm, item_code);
			return;
		}

		const response = await frappe.call({
			method:
				'navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip.get_draft_delivery_note_items',
			args: { tote: frm.doc.custom_tote },
		});

		const items = response.message || [];

		if (!items.length) {
			frappe.show_alert(
				{
					message: __('No items found for tote {0}', [frm.doc.custom_tote]),
					indicator: 'red',
				},
				5,
			);
			return;
		}

		const matching_sources = [
			...new Set(
				items
					.filter((item) => item.item_code === item_code)
					.map((item) => item.delivery_note),
			),
		];

		if (!matching_sources.length) {
			frappe.show_alert(
				{
					message: __('Item {0} not found in any Delivery Note for tote {1}', [
						item_code,
						frm.doc.custom_tote,
					]),
					indicator: 'red',
				},
				5,
			);
			return;
		}

		if (matching_sources.length === 1) {
			await set_delivery_note_and_load_items(
				frm,
				matching_sources[0],
				item_code,
			);
		} else {
			show_source_selection_dialog(frm, matching_sources, item_code);
		}
	} catch {
		frappe.show_alert(
			{ message: __('Error scanning barcode'), indicator: 'red' },
			5,
		);
	}
}

function show_source_selection_dialog(frm, sources, item_code) {
	const dialog = new frappe.ui.Dialog({
		title: __('Multiple Delivery Notes Found'),
		fields: [
			{
				fieldname: 'source',
				fieldtype: 'Select',
				label: __('Select Delivery Note'),
				options: sources.join('\n'),
				reqd: 1,
				default: sources[0],
			},
		],
		primary_action_label: __('Select'),
		async primary_action({ source }) {
			dialog.hide();
			await set_delivery_note_and_load_items(frm, source, item_code);
		},
	});
	dialog.show();
}

async function set_delivery_note_and_load_items(
	frm,
	source_name,
	scanned_item_code,
) {
	frm.selected_delivery_note = source_name;

	await frm.set_value('delivery_note', source_name);
	frm.toggle_enable('delivery_note', false);

	const response = await frappe.call({
		method:
			'navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip.get_delivery_note_items',
		args: { delivery_note: source_name },
	});

	const items = response.message || [];

	frm.clear_table('items');
	for (const item of items) {
		const row = frm.add_child('items');
		row.item_code = item.item_code;
		row.item_name = item.item_name;
		row.description = item.description;
		row.qty = flt(item.qty);
		row.stock_uom = item.uom;
		row.dn_detail = item.dn_detail || '';
		row.custom_packed_qty = 0;
		if (item.weight_per_unit) {
			row.net_weight = flt(item.weight_per_unit) * flt(item.qty);
			row.weight_uom = item.weight_uom;
		}
	}

	if (!frm.doc.from_case_no) {
		frm.set_value('from_case_no', 1);
		frm.set_value('to_case_no', 1);
	}

	frm.refresh_field('items');
	frm.dirty();

	show_packed_qty_dialog(frm, scanned_item_code);
}

function show_packed_qty_dialog(frm, item_code) {
	const row = (frm.doc.items || []).find((r) => r.item_code === item_code);

	if (!row) {
		frappe.show_alert(
			{
				message: __('Item {0} not found in the items table', [item_code]),
				indicator: 'red',
			},
			5,
		);
		return;
	}

	const current_packed = flt(row.custom_packed_qty);
	const required_qty = flt(row.qty);
	const remaining = required_qty - current_packed;

	const dialog = new frappe.ui.Dialog({
		title: __('Update Packed Qty'),
		fields: [
			{
				fieldname: 'item_code',
				fieldtype: 'Data',
				label: __('Item Code'),
				read_only: 1,
				default: item_code,
			},
			{
				fieldname: 'item_name',
				fieldtype: 'Data',
				label: __('Item Name'),
				read_only: 1,
				default: row.item_name,
			},
			{
				fieldname: 'required_qty',
				fieldtype: 'Data',
				label: __('Required Qty'),
				read_only: 1,
				default: cstr(required_qty),
			},
			{
				fieldname: 'current_packed_qty',
				fieldtype: 'Data',
				label: __('Already Packed'),
				read_only: 1,
				default: cstr(current_packed),
			},
			{
				fieldname: 'qty',
				fieldtype: 'Float',
				label: __('Qty to Pack'),
				default: remaining > 0 ? remaining : 1,
				reqd: 1,
			},
		],
		primary_action_label: __('Confirm'),
		primary_action({ qty }) {
			const new_packed_qty = current_packed + flt(qty);

			if (new_packed_qty > required_qty) {
				frappe.show_alert(
					{
						message: __('Packed qty for {0} ({1}) exceeds required qty ({2})', [
							item_code,
							new_packed_qty,
							required_qty,
						]),
						indicator: 'orange',
					},
					5,
				);
			}

			row.custom_packed_qty = new_packed_qty;
			row.custom_is_scanned = 1;
			frm.refresh_field('items');
			frm.dirty();

			frappe.show_alert({
				message: __('Packed {0}: {1}/{2}', [
					item_code,
					new_packed_qty,
					required_qty,
				]),
				indicator: 'green',
			});

			dialog.hide();
		},
	});
	dialog.show();
	setTimeout(() => dialog.fields_dict.qty.$input.select(), 150);
}
