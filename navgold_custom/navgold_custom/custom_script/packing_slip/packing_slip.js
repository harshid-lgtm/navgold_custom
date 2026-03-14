// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Packing Slip', {
	refresh(frm) {
		toggle_fields(frm);
		add_scan_buttons(frm);
		add_sfp_label_button(frm);
		add_sfp_cancel_button(frm);
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

function add_sfp_label_button(frm) {
	if (frm.doc.docstatus === 2 || !frm.doc.delivery_note) return;

	const label_exists = !!frm.doc.custom_sfp_label;
	const button_label = label_exists
		? __('Regenerate SFP Label')
		: __('Get SFP Label');

	frm.add_custom_button(button_label, async () => {
		try {
			const rates_response = await frappe.call({
				method:
					'navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip.get_sfp_shipping_rates',
				args: { packing_slip: frm.doc.name },
			});

			const rates = rates_response.message || [];
			if (!rates.length) {
				frappe.show_alert(
					{ message: __('No shipping rates returned.'), indicator: 'red' },
					5,
				);
				return;
			}

			show_sfp_rate_dialog(frm, rates);
		} catch {
			frappe.show_alert(
				{ message: __('Failed to fetch SFP rates.'), indicator: 'red' },
				5,
			);
		}
	});
}

function add_sfp_cancel_button(frm) {
	if (frm.doc.docstatus === 2 || !frm.doc.custom_sfp_shipment_id) return;

	frm.add_custom_button(__('Cancel SFP Shipment'), async () => {
		try {
			await frappe.call({
				method:
					'navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip.cancel_sfp_shipment',
				args: { packing_slip: frm.doc.name },
			});

			await frm.reload_doc();
			frappe.show_alert(
				{ message: __('SFP shipment cancelled.'), indicator: 'green' },
				5,
			);
		} catch {
			frappe.show_alert(
				{ message: __('Failed to cancel SFP shipment.'), indicator: 'red' },
				5,
			);
		}
	});
}

function show_sfp_rate_dialog(frm, rates) {
	const rate_label_map = new Map();
	const options = rates.map((rate) => {
		const charge = rate.total_charge ? `£${rate.total_charge}` : '';
		const delivery_date = rate.delivery_window_end
			? rate.delivery_window_end.split('T')[0]
			: '';
		const delivery = delivery_date
			? ` — Delivery by ${frappe.datetime.str_to_user(delivery_date)}`
			: '';
		const label = `${rate.carrier_name || ''} — ${
			rate.service_name || ''
		} (${charge})${delivery}`;
		rate_label_map.set(label, rate);
		return label;
	});

	const dialog = new frappe.ui.Dialog({
		title: __('Select Shipping Rate'),
		fields: [
			{
				fieldname: 'rate',
				fieldtype: 'Select',
				label: __('Rate'),
				options: options.join('\n'),
				reqd: 1,
				default: options[0],
			},
		],
		primary_action_label: __('Buy Label'),
		async primary_action({ rate }) {
			dialog.hide();
			const selected_rate = rate_label_map.get(rate);
			if (!selected_rate?.rate_id) {
				frappe.show_alert(
					{ message: __('Invalid rate selection.'), indicator: 'red' },
					5,
				);
				return;
			}

			try {
				const response = await frappe.call({
					method:
						'navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip.purchase_sfp_shipping_label',
					args: {
						packing_slip: frm.doc.name,
						rate_id: selected_rate.rate_id,
						document_spec: selected_rate.document_spec || null,
						request_token: selected_rate.request_token || '',
						value_added_services: selected_rate.value_added_services || [],
					},
				});

				const result = response.message || {};
				await frm.reload_doc();

				if (result.file_url) {
					frappe.show_alert(
						{
							message: __('SFP label generated successfully.'),
							indicator: 'green',
						},
						5,
					);
				}
			} catch {
				frappe.show_alert(
					{ message: __('Failed to generate SFP label.'), indicator: 'red' },
					5,
				);
			}
		},
	});
	dialog.show();
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
