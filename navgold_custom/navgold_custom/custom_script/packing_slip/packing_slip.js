// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Packing Slip', {
	onload(frm) {
		frm.selected_delivery_note = null;
		frm._row_conversion_factors = {};
		frm.row_bundles = {};
		frm.row_bundle_descriptions = {};
	},
	refresh(frm) {
		toggle_fields(frm);
		add_scan_buttons(frm);
		add_sfp_label_button(frm);
		// Cancel SFP Shipment is hidden for now.
		// add_sfp_cancel_button(frm);
		add_pack_remaining_button(frm);
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
			navgold.packing.open_scan_tote_dialog(frm, 'custom_tote', false),
		);
	}

	if (frm.doc.custom_tote) {
		frm.add_custom_button(__('Scan Item Barcode'), () =>
			navgold.packing.open_scan_item_dialog(frm, process_item_scan, false),
		);
	}
}

function add_sfp_label_button(frm) {
	if (frm.is_new() || frm.doc.docstatus === 2 || !frm.doc.delivery_note) return;

	const label_exists = !!frm.doc.custom_sfp_label;
	const button_label = label_exists
		? __('Regenerate SFP Label')
		: __('Get SFP Label');

	frm.add_custom_button(button_label, async () => {
		try {
			const rates_response = await frappe.call({
				method:
					'navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip_api.get_sfp_shipping_rates',
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
			// frappe.call already shows the server error dialog
		}
	});
}

function add_sfp_cancel_button(frm) {
	if (frm.doc.docstatus === 2 || !frm.doc.custom_sfp_shipment_id) return;

	frm.add_custom_button(__('Cancel SFP Shipment'), async () => {
		try {
			await frappe.call({
				method:
					'navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip_api.cancel_sfp_shipment',
				args: { packing_slip: frm.doc.name },
			});

			await frm.reload_doc();
			frappe.show_alert(
				{ message: __('SFP shipment cancelled.'), indicator: 'green' },
				5,
			);
		} catch {
			// frappe.call already shows the server error dialog
		}
	});
}

async function add_pack_remaining_button(frm) {
	if (frm.doc.docstatus !== 1 || !frm.doc.custom_tote) return;

	const response = await frappe.call({
		method:
			'navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip_api.get_draft_delivery_note_items',
		args: { tote: frm.doc.custom_tote },
	});

	const remaining_items = response.message || [];
	if (!remaining_items.length) return;

	frm.add_custom_button(__('Pack Remaining Items'), () => {
		frappe.new_doc('Packing Slip', { custom_tote: frm.doc.custom_tote });
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
						'navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip_api.purchase_sfp_shipping_label',
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
					window.open(result.file_url, '_blank');
				}
			} catch {
				// frappe.call already shows the server error dialog
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
				'navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip_api.get_draft_delivery_note_items',
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
	navgold.packing.prompt_select({
		title: __('Multiple Delivery Notes Found'),
		label: __('Select Delivery Note'),
		options: sources,
		on_select: (source) =>
			set_delivery_note_and_load_items(frm, source, item_code),
	});
}

async function set_delivery_note_and_load_items(
	frm,
	source_name,
	scanned_item_code,
) {
	frm.selected_delivery_note = source_name;

	frm.doc.delivery_note = source_name;
	frm.refresh_field('delivery_note');
	frm.toggle_enable('delivery_note', false);

	const response = await frappe.call({
		method:
			'navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip_api.get_delivery_note_items',
		args: { delivery_note: source_name },
	});

	const items = response.message || [];

	frm._row_conversion_factors = {};
	frm.row_bundles = {};
	frm.row_bundle_descriptions = {};

	frm.clear_table('items');
	for (const item of items) {
		const row = frm.add_child('items');
		row.item_code = item.item_code;
		row.item_name = item.item_name;
		row.description = item.description;
		row.qty = flt(item.qty);
		row.stock_uom = item.uom;
		row.dn_detail = item.dn_detail || '';
		row.pi_detail = item.pi_detail || '';
		row.custom_packed_qty = 0;

		const row_key = item.dn_detail || item.pi_detail;
		frm._row_conversion_factors[row_key] = flt(item.conversion_factor) || 1;

		if (item.bundle) {
			frm.row_bundles[item.pi_detail] = item.bundle;
			frm.row_bundle_descriptions[item.pi_detail] = item.bundle_description;
		}

		if (item.weight_per_unit) {
			row.net_weight = flt(item.weight_per_unit);
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

function get_row_conversion_factor(frm, row) {
	return (
		flt(frm._row_conversion_factors?.[row.dn_detail || row.pi_detail]) || 1
	);
}

function show_packed_qty_dialog(frm, item_code) {
	const matching_rows = (frm.doc.items || []).filter(
		(r) => r.item_code === item_code,
	);

	if (!matching_rows.length) {
		frappe.show_alert(
			{
				message: __('Item {0} not found in the items table', [item_code]),
				indicator: 'red',
			},
			5,
		);
		return;
	}

	const bundles = [
		...new Set(
			matching_rows.map((r) => frm.row_bundles?.[r.pi_detail]).filter(Boolean),
		),
	];
	const has_standalone = matching_rows.some(
		(r) => !frm.row_bundles?.[r.pi_detail],
	);

	const targets = [
		...bundles.map((bundle) => ({ type: 'bundle', bundle })),
		...(has_standalone ? [{ type: 'standalone' }] : []),
	];

	if (targets.length === 1) {
		dispatch_pack_target(frm, targets[0], item_code);
	} else {
		show_pack_target_selection_dialog(frm, targets, item_code);
	}
}

function dispatch_pack_target(frm, target, item_code) {
	if (target.type === 'bundle') {
		const bundle_rows = (frm.doc.items || []).filter(
			(r) => frm.row_bundles?.[r.pi_detail] === target.bundle,
		);
		const bundle_description = bundle_rows.length
			? frm.row_bundle_descriptions?.[bundle_rows[0].pi_detail] || ''
			: '';
		show_pack_qty_dialog(frm, bundle_rows, target.bundle, {
			is_bundle: true,
			description: bundle_description,
		});
		return;
	}

	const standalone_rows = (frm.doc.items || []).filter(
		(r) => r.item_code === item_code && !frm.row_bundles?.[r.pi_detail],
	);
	show_pack_qty_dialog(frm, standalone_rows, item_code);
}

function show_pack_target_selection_dialog(frm, targets, item_code) {
	const option_map = new Map();
	const options = targets.map((target) => {
		const label =
			target.type === 'bundle'
				? __('Bundle: {0}', [target.bundle])
				: __('Standalone item: {0}', [item_code]);
		option_map.set(label, target);
		return label;
	});

	navgold.packing.prompt_select({
		title: __('Item belongs to multiple sources'),
		label: __('Select what to pack'),
		options,
		on_select: (value) =>
			dispatch_pack_target(frm, option_map.get(value), item_code),
	});
}

function show_pack_qty_dialog(frm, matching_rows, item_code, options = {}) {
	if (!matching_rows.length) {
		frappe.show_alert(
			{
				message: __('Item {0} not found in the items table', [item_code]),
				indicator: 'red',
			},
			5,
		);
		return;
	}

	let total_required_nos = 0;
	let total_packed_nos = 0;
	let total_required_uom = 0;

	const original_uom = matching_rows[0].stock_uom || 'Nos';

	for (const row of matching_rows) {
		const conversion_factor = get_row_conversion_factor(frm, row);
		total_required_nos += flt(row.qty) * conversion_factor;
		total_packed_nos += flt(row.custom_packed_qty) * conversion_factor;
		total_required_uom += flt(row.qty);
	}

	const qty_precision = precision('qty', matching_rows[0]);
	total_required_nos = flt(total_required_nos, qty_precision);
	total_packed_nos = flt(total_packed_nos, qty_precision);

	const remaining_nos = total_required_nos - total_packed_nos;
	const show_original_uom =
		original_uom !== 'Nos' && total_required_uom !== total_required_nos;

	const description = options.is_bundle
		? options.description
		: matching_rows[0].description;

	let scan_next_after_hide = false;

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
				fieldname: 'description',
				fieldtype: 'Small Text',
				label: __('Description'),
				read_only: 1,
				default: description,
			},
			{
				fieldname: 'original_uom_info',
				fieldtype: 'Data',
				label: __('Original UOM'),
				read_only: 1,
				default: `${total_required_uom} ${original_uom}`,
				hidden: !show_original_uom,
			},
			{
				fieldname: 'required_qty',
				fieldtype: 'Data',
				label: __('Required Qty (Nos)'),
				read_only: 1,
				default: cstr(total_required_nos),
			},
			{
				fieldname: 'current_packed_qty',
				fieldtype: 'Data',
				label: __('Already Packed (Nos)'),
				read_only: 1,
				default: cstr(total_packed_nos),
			},
			{
				fieldname: 'qty',
				fieldtype: 'Float',
				label: __('Qty to Pack (Nos)'),
				default: remaining_nos > 0 ? remaining_nos : 1,
				reqd: 1,
			},
		],
		primary_action_label: __('Confirm'),
		primary_action({ qty }) {
			const apply_packed_qty = () => {
				const new_total_packed_nos = total_packed_nos + flt(qty);

				if (new_total_packed_nos > total_required_nos) {
					frappe.show_alert(
						{
							message: __(
								'Packed qty for {0} ({1}) exceeds required qty ({2})',
								[item_code, new_total_packed_nos, total_required_nos],
							),
							indicator: 'orange',
						},
						5,
					);
				}

				distribute_packed_qty(frm, matching_rows, new_total_packed_nos);

				frm.refresh_field('items');
				frm.dirty();

				frappe.show_alert({
					message: __('Packed {0}: {1}/{2}', [
						item_code,
						new_total_packed_nos,
						total_required_nos,
					]),
					indicator: 'green',
				});
			};

			if (options.is_bundle) {
				// Close this dialog BEFORE stacking frappe.confirm on top of it. A modal-on-modal
				// leaves the confirm's Yes button unresponsive in some browsers, so apply_packed_qty
				// never runs, custom_packed_qty stays 0, and the "Product Bundle" confirm loops
				// forever — a bundle Packing Slip could never be packed/saved.
				dialog.hide();

				let bundle_confirmed = false;
				const confirm_dialog = frappe.confirm(
					__('The Item is a Product Bundle.'),
					() => {
						apply_packed_qty();
						bundle_confirmed = true;
					},
				);
				confirm_dialog.on_hide = () => {
					if (bundle_confirmed) prompt_next_item_scan(frm);
				};
			} else {
				apply_packed_qty();
				scan_next_after_hide = true;
				dialog.hide();
			}
		},
		on_hide() {
			if (scan_next_after_hide) prompt_next_item_scan(frm);
		},
	});
	dialog.show();
	setTimeout(() => dialog.fields_dict.qty.$input.select(), 150);
}

function is_fully_packed(frm) {
	const rows = frm.doc.items || [];
	if (!rows.length) return false;

	return rows.every(
		(row) =>
			flt(row.custom_packed_qty, precision('custom_packed_qty', row)) >=
			flt(row.qty, precision('qty', row)),
	);
}

function prompt_next_item_scan(frm) {
	if (is_fully_packed(frm)) {
		frappe.show_alert(
			{
				message: __('All items packed. Save the Packing Slip.'),
				indicator: 'blue',
			},
			5,
		);
		return;
	}

	navgold.packing.open_scan_item_dialog(frm, process_item_scan, false);
}

function distribute_packed_qty(frm, rows, total_packed_nos) {
	let remaining_nos = total_packed_nos;

	for (const row of rows) {
		const conversion_factor = get_row_conversion_factor(frm, row);
		const row_capacity_nos = flt(row.qty) * conversion_factor;
		const packed_nos_for_row = Math.min(remaining_nos, row_capacity_nos);

		row.custom_packed_qty = flt(
			packed_nos_for_row / conversion_factor,
			precision('custom_packed_qty', row),
		);
		row.custom_is_scanned = packed_nos_for_row > 0 ? 1 : 0;
		remaining_nos -= packed_nos_for_row;
	}
}
