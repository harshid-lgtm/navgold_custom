// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('FBA Inbound', {
	setup(frm) {
		frm.set_query('stock_entry', () => ({
			filters: { docstatus: 1, stock_entry_type: 'FBA Transfer' },
		}));
	},

	refresh(frm) {
		add_action_buttons(frm);
		setup_realtime_listeners(frm);
	},

	async stock_entry(frm) {
		if (!frm.doc.stock_entry) return;

		await load_items_and_boxes(frm);
	},
});

async function load_items_and_boxes(frm) {
	frm.clear_table('items');
	frm.clear_table('boxes');
	frm.refresh_field('items');
	frm.refresh_field('boxes');

	const [items_response, boxes_response] = await Promise.all([
		frappe.call({
			method:
				'navgold_custom.navgold_custom.doctype.fba_inbound.fba_inbound.get_items_from_stock_entry',
			args: { stock_entry_name: frm.doc.stock_entry },
		}),
		frappe.call({
			method:
				'navgold_custom.navgold_custom.doctype.fba_inbound.fba_inbound.get_boxes_from_packing_slips',
			args: { stock_entry_name: frm.doc.stock_entry },
		}),
	]);

	const items = items_response.message || [];
	for (const item of items) {
		frm.add_child('items', item);
	}
	frm.refresh_field('items');
	frm.dirty();

	const boxes = boxes_response.message || [];
	for (const box of boxes) {
		frm.add_child('boxes', box);
	}
	frm.refresh_field('boxes');
	frm.dirty();

	if (!items.length) {
		frappe.show_alert(
			{
				message: __('No FBA Proxy SKUs found for items in this Stock Entry'),
				indicator: 'orange',
			},
			6,
		);
	}

	if (!boxes.length) {
		frappe.show_alert(
			{
				message: __(
					'No submitted FBA Packing Slips found for this Stock Entry',
				),
				indicator: 'orange',
			},
			6,
		);
	}

	frm.dirty();
}

function add_action_buttons(frm) {
	if (frm.doc.docstatus !== 0) return;

	const status = frm.doc.status;

	if (status === 'Draft' && frm.doc.items?.length) {
		frm.add_custom_button(
			__('Create Plan'),
			() => create_plan(frm),
			__('Amazon'),
		);
	}

	if (status === 'Plan Created') {
		frm.add_custom_button(
			__('Get Packing Options'),
			() => fetch_packing_options(frm),
			__('Amazon'),
		);
	}

	if (status === 'Packing Options Ready') {
		frm.add_custom_button(
			__('Confirm Packing'),
			() => confirm_packing_option(frm),
			__('Amazon'),
		);
	}

	if (status === 'Packing Confirmed') {
		frm.add_custom_button(
			__('Get Placement Options'),
			() => fetch_placement_options(frm),
			__('Amazon'),
		);
	}

	if (status === 'Placement Options Ready') {
		frm.add_custom_button(
			__('Confirm Placement'),
			() => confirm_placement_option(frm),
			__('Amazon'),
		);
	}

	if (status === 'Placement Confirmed') {
		frm.add_custom_button(
			__('Get Transport Options'),
			() => fetch_transportation_options(frm),
			__('Amazon'),
		);
	}

	if (status === 'Transport Options Ready') {
		frm.add_custom_button(
			__('Confirm Transport'),
			() => confirm_transportation_options(frm),
			__('Amazon'),
		);
	}

	if (status === 'Transport Confirmed' || status === 'Labels Ready') {
		frm.add_custom_button(
			__('Get Labels'),
			() => get_labels(frm),
			__('Amazon'),
		);
	}

	if (status !== 'Cancelled' && status !== 'Shipped') {
		frm.add_custom_button(__('Cancel Plan'), () => cancel_plan(frm));
	}
}

async function create_plan(frm) {
	if (!frm.doc.amazon_sp_api_settings) {
		frappe.msgprint(
			__('Please select Amazon SP API Settings before creating a plan.'),
		);
		return;
	}

	if (!frm.doc.items?.length) {
		frappe.msgprint(__('Add items before creating the plan.'));
		return;
	}

	const confirm_plan_creation = await confirm_dialog(
		__('This will create an FBA Inbound Plan on Amazon. Proceed?'),
	);
	if (!confirm_plan_creation) return;

	await frm.call('create_plan');
}

async function fetch_packing_options(frm) {
	const confirm_fetch_packing = await confirm_dialog(
		__('Generate packing options from Amazon? This will take ~30 seconds.'),
	);
	if (!confirm_fetch_packing) return;

	await frm.call('fetch_packing_options');
}

async function confirm_packing_option(frm) {
	const selected = (frm.doc.packing_options || []).filter(
		(row) => row.is_selected,
	);
	if (!selected.length) {
		frappe.msgprint(__('Tick "Select" on one packing option first.'));
		return;
	}

	const confirm_packing_option = await confirm_dialog(
		__('Confirm the selected packing option and send box details to Amazon?'),
	);
	if (!confirm_packing_option) return;

	await frm.call('confirm_packing_option');
}

async function fetch_placement_options(frm) {
	const confirm_fetch_placement = await confirm_dialog(
		__(
			'Generate placement options from Amazon? Amazon will assign shipments to fulfilment centres.',
		),
	);
	if (!confirm_fetch_placement) return;

	await frm.call('fetch_placement_options');
}

async function confirm_placement_option(frm) {
	const selected = (frm.doc.placement_options || []).filter(
		(row) => row.is_selected,
	);
	if (!selected.length) {
		frappe.msgprint(__('Tick "Select" on one placement option first.'));
		return;
	}

	const confirm_placement_option = await confirm_dialog(
		__(
			'Confirm the selected placement option? This will create actual Amazon shipments.',
		),
	);
	if (!confirm_placement_option) return;

	await frm.call('confirm_placement_option');
}

async function confirm_transportation_options(frm) {
	const selected = (frm.doc.transportation_options || []).filter(
		(row) => row.is_selected,
	);
	if (!selected.length) {
		frappe.msgprint(__('Tick "Select" on a transportation option first.'));
		return;
	}

	const transport_confirmed = await confirm_dialog(
		__('Confirm the selected transport option(s) with Amazon?'),
	);
	if (!transport_confirmed) return;

	await frm.call('confirm_transportation_options');
}

async function get_labels(frm) {
	const result = await new Promise((resolve) => {
		const dialog = new frappe.ui.Dialog({
			title: __('Download Box Labels'),
			fields: [
				{
					fieldname: 'page_type',
					fieldtype: 'Select',
					label: __('Label Page Type'),
					options: [
						'PackageLabel_Plain_Paper',
						'PackageLabel_Plain_Paper_Default',
						'PackageLabel_Thermal',
						'PackageLabel_Thermal_NonPCP',
						'PackageLabel_A4_4',
						'PackageLabel_A4_6',
						'PackageLabel_A4_2_DHL',
						'PackageLabel_A4_2_AMZL',
						'PackageLabel_A4_2_Standard_Carrier',
					].join('\n'),
					default: 'PackageLabel_Plain_Paper',
					reqd: 1,
				},
				{
					fieldname: 'include_pallet',
					fieldtype: 'Check',
					label: __('Also download Pallet Labels (LTL/FTL shipments only)'),
					default: 0,
					description: __(
						'LTL/FTL shipments only. Requires "Shipping and Fulfillment" SP API role. Not needed for GROUND_SMALL_PARCEL.',
					),
				},
			],
			primary_action_label: __('Download'),
			primary_action({ page_type, include_pallet }) {
				dialog.hide();
				resolve({ page_type, include_pallet });
			},
			secondary_action() {
				dialog.hide();
				resolve(null);
			},
		});
		dialog.show();
	});

	if (!result) return;

	await frm.call('get_labels', {
		page_type: result.page_type,
		include_pallet: result.include_pallet,
	});
}

async function fetch_transportation_options(frm) {
	const ready_date = await new Promise((resolve) => {
		const dialog = new frappe.ui.Dialog({
			title: __('Ready to Ship Date'),
			fields: [
				{
					fieldname: 'ready_to_ship_date',
					fieldtype: 'Date',
					label: __('Ready to Ship Date'),
					reqd: 1,
					default: frappe.datetime.add_days(frappe.datetime.get_today(), 1),
				},
			],
			primary_action_label: __('Fetch Options'),
			primary_action({ ready_to_ship_date }) {
				dialog.hide();
				resolve(ready_to_ship_date);
			},
			secondary_action() {
				dialog.hide();
				resolve(null);
			},
		});
		dialog.show();
	});

	if (!ready_date) return;

	await frm.call('fetch_transportation_options', {
		ready_to_ship_date: ready_date,
	});
}

async function cancel_plan(frm) {
	const has_plan = !!frm.doc.inbound_plan_id;
	const msg = has_plan
		? __(
				'This will cancel the inbound plan on Amazon ({0}) and mark this document as Cancelled. Proceed?',
				[frm.doc.inbound_plan_id],
		  )
		: __('No Amazon plan exists yet. Mark this document as Cancelled?');

	const confirm_cancel = await confirm_dialog(msg);
	if (!confirm_cancel) return;

	await frm.call('cancel_plan');

	if (!has_plan) frm.reload_doc();
}

const FBA_REALTIME_EVENTS = [
	'fba_inbound_plan_created',
	'fba_inbound_plan_failed',
	'fba_inbound_packing_options_ready',
	'fba_inbound_packing_confirmed',
	'fba_inbound_placement_options_ready',
	'fba_inbound_placement_confirmed',
	'fba_inbound_transport_options_ready',
	'fba_inbound_transport_confirmed',
	'fba_inbound_labels_ready',
	'fba_inbound_cancelled',
	'fba_inbound_step_failed',
];

function setup_realtime_listeners(frm) {
	if (frm._fba_realtime_setup) return;
	frm._fba_realtime_setup = true;

	for (const event of FBA_REALTIME_EVENTS) {
		frappe.realtime.off(event);
	}

	frappe.realtime.on('fba_inbound_plan_created', (data) => {
		if (data.name !== frm.doc.name) return;

		frappe.show_alert(
			{
				message: __('FBA Inbound Plan created: {0}', [data.inbound_plan_id]),
				indicator: 'green',
			},
			8,
		);
		frm.reload_doc();
	});

	frappe.realtime.on('fba_inbound_plan_failed', (data) => {
		if (data.name !== frm.doc.name) return;

		frappe.show_alert(
			{
				message: __('Plan creation failed: {0}', [data.error]),
				indicator: 'red',
			},
			10,
		);
	});

	frappe.realtime.on('fba_inbound_packing_options_ready', (data) => {
		if (data.name !== frm.doc.name) return;

		frappe.show_alert(
			{
				message: __(
					'Packing options ready: {0} option(s). Select one and click Confirm Packing.',
					[data.count],
				),
				indicator: 'green',
			},
			8,
		);
		frm.reload_doc();
	});

	frappe.realtime.on('fba_inbound_packing_confirmed', (data) => {
		if (data.name !== frm.doc.name) return;

		frappe.show_alert(
			{
				message: __('Packing confirmed. Box details sent to Amazon.'),
				indicator: 'green',
			},
			8,
		);
		frm.reload_doc();
	});

	frappe.realtime.on('fba_inbound_placement_options_ready', (data) => {
		if (data.name !== frm.doc.name) return;
		frappe.show_alert(
			{
				message: __(
					'Placement options ready: {0} option(s). Review FC assignments, then confirm.',
					[data.count],
				),
				indicator: 'green',
			},
			8,
		);
		frm.reload_doc();
	});

	frappe.realtime.on('fba_inbound_placement_confirmed', (data) => {
		if (data.name !== frm.doc.name) return;
		frappe.show_alert(
			{
				message: __('Placement confirmed. {0} shipment(s) created.', [
					data.shipment_count,
				]),
				indicator: 'green',
			},
			8,
		);
		frm.reload_doc();
	});

	frappe.realtime.on('fba_inbound_transport_options_ready', (data) => {
		if (data.name !== frm.doc.name) return;
		frappe.show_alert(
			{
				message: __(
					'Transportation options ready: {0} option(s). Review carriers and costs.',
					[data.count],
				),
				indicator: 'green',
			},
			8,
		);
		frm.reload_doc();
	});

	frappe.realtime.on('fba_inbound_cancelled', (data) => {
		if (data.name !== frm.doc.name) return;
		frappe.show_alert(
			{
				message: __('Inbound plan cancelled on Amazon successfully.'),
				indicator: 'green',
			},
			8,
		);
		frm.reload_doc();
	});

	frappe.realtime.on('fba_inbound_transport_confirmed', (data) => {
		if (data.name !== frm.doc.name) return;
		frappe.show_alert(
			{
				message: __('Transport confirmed. Ready to get labels.'),
				indicator: 'green',
			},
			8,
		);
		frm.reload_doc();
	});

	frappe.realtime.on('fba_inbound_labels_ready', (data) => {
		if (data.name !== frm.doc.name) return;
		frappe.show_alert(
			{
				message: __('Box labels downloaded and attached ({0} file(s)).', [
					data.label_urls?.length || 0,
				]),
				indicator: 'green',
			},
			8,
		);
		frm.reload_doc();
	});

	frappe.realtime.on('fba_inbound_step_failed', (data) => {
		if (data.name !== frm.doc.name) return;

		frappe.show_alert(
			{
				message: __('Step "{0}" failed: {1}', [data.step, data.error]),
				indicator: 'red',
			},
			12,
		);
	});
}
