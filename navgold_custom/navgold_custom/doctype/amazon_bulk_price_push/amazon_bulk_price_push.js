// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Amazon Bulk Price Push', {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.upload_file) {
			frm.add_custom_button(__('Load from File'), () => load_from_file(frm));
		}

		frm.add_custom_button(__('Add SKUs'), () => add_skus(frm), __('Add Rows'));
		frm.add_custom_button(
			__('Add by Filter'),
			() => add_by_filter(frm),
			__('Add Rows'),
		);

		if ((frm.doc.rows || []).length && frm.doc.status !== 'Pushed') {
			frm
				.add_custom_button(__('Dry Run'), () => dry_run(frm))
				.addClass('btn-primary');
			frm.add_custom_button(__('Push to Amazon'), () => push_live(frm));
		}
	},
});

frappe.ui.form.on('Amazon Bulk Price Push Item', {
	async proxy_sku(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.proxy_sku) return;

		// A picked Proxy SKU is also the seller SKU the feed carries.
		const { message } = await frappe.call({
			method:
				'navgold_custom.navgold_custom.doctype.amazon_bulk_price_push.amazon_bulk_price_push.fetch_pricing_for_bulk',
			args: { proxy_skus: [row.proxy_sku] },
		});
		const pricing = (message || [])[0];
		if (!pricing) return;

		frappe.model.set_value(cdt, cdn, 'sku', pricing.sku);
		frappe.model.set_value(cdt, cdn, 'min_price', pricing.min_price);
		frappe.model.set_value(cdt, cdn, 'max_price', pricing.max_price);
		frappe.model.set_value(cdt, cdn, 'our_price', pricing.our_price);
		frappe.model.set_value(cdt, cdn, 'price_source', pricing.price_source);
	},
});

async function load_from_file(frm) {
	const { message } = await frm.call({
		doc: frm.doc,
		method: 'load_from_file',
		freeze: true,
		freeze_message: __('Reading file...'),
	});
	frm.reload_doc();
	frappe.show_alert({
		message: __('Loaded {0} row(s) from file.', [message.loaded]),
		indicator: 'green',
	});
}

function add_skus(frm) {
	new frappe.ui.form.MultiSelectDialog({
		doctype: 'Proxy SKU',
		target: frm,
		setters: { is_active: 1 },
		add_filters_group: 1,
		async action(selections) {
			if (!selections.length) return;
			const { message } = await frm.call({
				doc: frm.doc,
				method: 'add_rows',
				args: { proxy_skus: selections },
				freeze: true,
				freeze_message: __('Adding SKUs...'),
			});
			frm.reload_doc();
			frappe.show_alert({
				message: __('Added {0} SKU(s).', [message.added]),
				indicator: 'green',
			});
		},
	});
}

function add_by_filter(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __('Add SKUs by Filter'),
		fields: [
			{
				fieldname: 'is_active',
				fieldtype: 'Check',
				label: __('Active Only'),
				default: 1,
			},
			{
				fieldname: 'active_strategy',
				fieldtype: 'Link',
				options: 'Amazon Product Strategy',
				label: __('Active Strategy'),
				description: __('Leave blank for any strategy.'),
			},
		],
		primary_action_label: __('Add Matching SKUs'),
		async primary_action(values) {
			const filters = {};
			if (cint(values.is_active)) filters.is_active = 1;
			if (values.active_strategy)
				filters.active_strategy = values.active_strategy;

			const { message } = await frm.call({
				doc: frm.doc,
				method: 'add_rows',
				args: { filters },
				freeze: true,
				freeze_message: __('Finding SKUs...'),
			});
			dialog.hide();
			frm.reload_doc();
			frappe.show_alert({
				message: __('Added {0} SKU(s).', [message.added]),
				indicator: 'green',
			});
		},
	});
	dialog.show();
}

async function dry_run(frm) {
	const { message } = await frm.call({
		doc: frm.doc,
		method: 'dry_run',
		freeze: true,
		freeze_message: __('Validating prices...'),
	});
	frm.reload_doc();
	frappe.msgprint({
		title: __('Dry Run Passed'),
		indicator: 'green',
		message: __('{0} SKU(s) validated. Review the grid, then Push to Amazon.', [
			message.sku_count,
		]),
	});
}

async function push_live(frm) {
	const count = (frm.doc.rows || []).length;
	const confirmed = await navgold.confirm_dialog(
		__(
			'Push manual prices for {0} SKU(s) to Amazon? Guards still apply and this cannot be undone.',
			[count],
		),
	);
	if (!confirmed) return;

	const { message } = await frm.call({
		doc: frm.doc,
		method: 'push_live',
		freeze: true,
		freeze_message: __('Submitting feed to Amazon...'),
	});
	frm.reload_doc();
	frappe.msgprint(
		__('Submitted feed {0} (log {1}) for {2} SKU(s).', [
			message.feed_id,
			message.log_name,
			message.sku_count,
		]),
	);
}
