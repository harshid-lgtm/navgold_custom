// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Navgold Settings', {
	refresh(frm) {
		// Amazon Pricing buttons (only show when pricing is enabled)
		if (frm.doc.amazon_pricing_enabled) {
			// Sync Repricer Rules button
			if (frm.doc.repricer_enabled) {
				frm.add_custom_button(
					__('Sync Repricer Rules'),
					() => {
						frappe.call({
							method:
								'navgold_custom.navgold_custom.amazon_pricing.repricer_client.sync_repricer_rules',
							freeze: true,
							freeze_message: __('Syncing rules from Repricer.com...'),
							callback: (r) => {
								if (r.message) {
									if (r.message.success) {
										frappe.msgprint(
											__('Synced {0} rules, created {1} new rules', [
												r.message.synced,
												r.message.created,
											]),
										);
									} else {
										frappe.msgprint(__('Sync failed: {0}', [r.message.error]));
									}
								}
							},
						});
					},
					__('Amazon Pricing'),
				);
			}

			// Bulk COGS Sync button
			if (frm.doc.auto_sync_cogs && frm.doc.cogs_supplier) {
				frm.add_custom_button(
					__('Bulk Sync COGS'),
					() => {
						frappe.confirm(
							__(
								"This will sync COGS from supplier '{0}' to all active pricing records. Continue?",
								[frm.doc.cogs_supplier],
							),
							() => {
								frappe.call({
									method:
										'navgold_custom.navgold_custom.amazon_pricing.cogs_sync.bulk_sync_cogs',
									args: {
										supplier: frm.doc.cogs_supplier,
									},
									freeze: true,
									freeze_message: __('Syncing COGS...'),
									callback: (r) => {
										if (r.message) {
											frappe.msgprint(
												__('Synced {0} records, {1} errors', [
													r.message.synced,
													r.message.errors,
												]),
											);
										}
									},
								});
							},
						);
					},
					__('Amazon Pricing'),
				);
			}

			// Refresh All Strategies button
			frm.add_custom_button(
				__('Refresh All Strategies'),
				() => {
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
							}
						},
					});
				},
				__('Amazon Pricing'),
			);
		}
	},

	async generate_proxy_skus(frm) {
		frm.call({
			doc: frm.doc,
			method: 'generate_proxy_skus_for_items',
		});
		frappe.show_alert(
			{ message: __('Proxy SKUs generation started'), indicator: 'blue' },
			10,
		);
	},
});
