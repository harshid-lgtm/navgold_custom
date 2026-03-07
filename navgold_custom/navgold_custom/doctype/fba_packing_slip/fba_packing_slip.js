// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('FBA Packing Slip', {
	setup(frm) {
		frm.set_query('stock_entry', () => ({
			filters: { docstatus: 0, stock_entry_type: 'FBA Transfer' },
		}));
	},

	refresh(frm) {
		add_scan_buttons(frm);
	},
});

function add_scan_buttons(frm) {
	if (frm.doc.docstatus !== 0) return;

	if (!frm.doc.tote) {
		frm.add_custom_button(__('Scan Tote'), () =>
			navgold.packing.open_scan_tote_dialog(frm, 'tote'),
		);
	}

	if (frm.doc.tote) {
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

		if (frm.selected_stock_entry) {
			await show_packed_qty_dialog(frm, item_code);
			return;
		}

		const response = await frappe.call({
			method:
				'navgold_custom.navgold_custom.doctype.fba_packing_slip.fba_packing_slip.get_draft_stock_entry_items',
			args: { tote: frm.doc.tote },
		});

		const items = response.message || [];

		if (!items.length) {
			frappe.show_alert(
				{
					message: __('No items found for tote {0}', [frm.doc.tote]),
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
					.map((item) => item.stock_entry),
			),
		];

		if (!matching_sources.length) {
			frappe.show_alert(
				{
					message: __('Item {0} not found in any Stock Entry for tote {1}', [
						item_code,
						frm.doc.tote,
					]),
					indicator: 'red',
				},
				5,
			);
			return;
		}

		if (matching_sources.length === 1) {
			await set_stock_entry_and_load_items(frm, matching_sources[0], item_code);
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
		title: __('Multiple Stock Entries Found'),
		fields: [
			{
				fieldname: 'source',
				fieldtype: 'Select',
				label: __('Select Stock Entry'),
				options: sources.join('\n'),
				reqd: 1,
				default: sources[0],
			},
		],
		primary_action_label: __('Select'),
		async primary_action({ source }) {
			dialog.hide();
			await set_stock_entry_and_load_items(frm, source, item_code);
		},
	});
	dialog.show();
}

async function set_stock_entry_and_load_items(
	frm,
	source_name,
	scanned_item_code,
) {
	frm.selected_stock_entry = source_name;

	await frm.set_value('stock_entry', source_name);
	frm.toggle_enable('stock_entry', false);

	const response = await frappe.call({
		method:
			'navgold_custom.navgold_custom.doctype.fba_packing_slip.fba_packing_slip.get_stock_entry_items',
		args: { stock_entry: source_name },
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

	await show_packed_qty_dialog(frm, scanned_item_code);
}

async function show_packed_qty_dialog(frm, item_code) {
	const row = (frm.doc.items || []).find((row) => row.item_code === item_code);

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

	const factor_response = await frappe.call({
		method:
			'navgold_custom.navgold_custom.doctype.fba_packing_slip.fba_packing_slip.get_singles_conversion_factor',
		args: { item_code },
	});
	const singles_factor = flt(factor_response.message) || 1;

	const current_packed = flt(row.custom_packed_qty);
	const required_qty = flt(row.qty);
	const remaining = required_qty - current_packed;

	const current_packed_singles = Math.round(current_packed * singles_factor);
	const required_qty_singles = Math.round(required_qty * singles_factor);
	const remaining_singles = Math.round(remaining * singles_factor);

	const dialog = new frappe.ui.Dialog({
		title: __('Update Packed Qty (Singles)'),
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
				label: __('Required Qty (Singles)'),
				read_only: 1,
				default: cstr(required_qty_singles),
			},
			{
				fieldname: 'current_packed_qty',
				fieldtype: 'Data',
				label: __('Already Packed (Singles)'),
				read_only: 1,
				default: cstr(current_packed_singles),
			},
			{
				fieldname: 'qty',
				fieldtype: 'Int',
				label: __('Qty to Pack (Singles)'),
				default: remaining_singles > 0 ? remaining_singles : 1,
				reqd: 1,
			},
		],
		primary_action_label: __('Confirm'),
		async primary_action({ qty }) {
			const qty_in_uom = flt(qty) / singles_factor;
			const new_packed_qty = current_packed + qty_in_uom;
			const new_packed_singles = Math.round(new_packed_qty * singles_factor);

			if (new_packed_qty > required_qty) {
				frappe.show_alert(
					{
						message: __('Packed qty for {0} ({1}) exceeds required qty ({2})', [
							item_code,
							new_packed_singles,
							required_qty_singles,
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
				message: __('Packed {0}: {1}/{2} singles', [
					item_code,
					new_packed_singles,
					required_qty_singles,
				]),
				indicator: 'green',
			});

			dialog.hide();
		},
	});
	dialog.show();
	setTimeout(() => dialog.fields_dict.qty.$input.select(), 150);
}
