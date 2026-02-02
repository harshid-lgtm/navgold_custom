// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.listview_settings['Amazon Product Pricing'] = {
	add_fields: [
		'is_active',
		'amazon_sync_status',
		'current_min_price',
		'current_max_price',
	],

	get_indicator: (doc) => {
		if (!doc.is_active) {
			return [__('Inactive'), 'grey', 'is_active,=,0'];
		}

		const status_map = {
			Pending: ['Pending', 'orange', 'amazon_sync_status,=,Pending'],
			Submitted: ['Submitted', 'green', 'amazon_sync_status,=,Submitted'],
			Error: ['Error', 'red', 'amazon_sync_status,=,Error'],
		};

		return status_map[doc.amazon_sync_status] || ['Unknown', 'grey', ''];
	},

	onload: (listview) => {
		// Bulk Submit to Amazon action
		listview.page.add_action_item(__('Submit to Amazon'), () => {
			const selected = listview.get_checked_items();
			if (selected.length === 0) {
				frappe.msgprint(__('Please select at least one record'));
				return;
			}

			const pricing_names = selected.map((d) => d.name);

			frappe.confirm(
				__('Submit {0} pricing record(s) to Amazon?', [pricing_names.length]),
				() => {
					frappe.call({
						method:
							'navgold_custom.navgold_custom.amazon_pricing.amazon_feed_submitter.bulk_submit_pricing',
						args: {
							pricing_names: JSON.stringify(pricing_names),
						},
						freeze: true,
						freeze_message: __('Submitting to Amazon...'),
						callback: (r) => {
							if (r.message) {
								if (r.message.success) {
									frappe.msgprint(
										__('Successfully submitted {0} products. Feed ID: {1}', [
											r.message.message_count,
											r.message.feed_id || 'N/A',
										]),
									);
								} else {
									frappe.msgprint(
										__('Submission failed: {0}', [r.message.error]),
									);
								}
								listview.refresh();
							}
						},
					});
				},
			);
		});

		// Bulk Sync to Repricer action
		listview.page.add_action_item(__('Sync to Repricer'), () => {
			const selected = listview.get_checked_items();
			if (selected.length === 0) {
				frappe.msgprint(__('Please select at least one record'));
				return;
			}

			const pricing_names = selected.map((d) => d.name);

			frappe.call({
				method:
					'navgold_custom.navgold_custom.amazon_pricing.repricer_client.bulk_sync_to_repricer',
				args: {
					pricing_names: JSON.stringify(pricing_names),
				},
				freeze: true,
				freeze_message: __('Syncing to Repricer...'),
				callback: (r) => {
					if (r.message) {
						if (r.message.success) {
							frappe.msgprint(
								__('Successfully synced {0} products to Repricer', [
									r.message.synced,
								]),
							);
						} else {
							frappe.msgprint(__('Sync failed: {0}', [r.message.error]));
						}
						listview.refresh();
					}
				},
			});
		});

		// Bulk Refresh Strategies action
		listview.page.add_action_item(__('Refresh Strategies'), () => {
			frappe.call({
				method:
					'navgold_custom.navgold_custom.amazon_pricing.strategy_selector.bulk_refresh_strategies',
				freeze: true,
				freeze_message: __('Refreshing strategies...'),
				callback: (r) => {
					if (r.message) {
						frappe.msgprint(
							__('Refreshed {0} records, {1} updated', [
								r.message.total,
								r.message.updated,
							]),
						);
						listview.refresh();
					}
				},
			});
		});
	},

	formatters: {
		current_min_price: (value) => {
			if (value) {
				return frappe.format(value, { fieldtype: 'Currency' });
			}
			return '-';
		},
		current_max_price: (value) => {
			if (value) {
				return frappe.format(value, { fieldtype: 'Currency' });
			}
			return '-';
		},
	},
};
