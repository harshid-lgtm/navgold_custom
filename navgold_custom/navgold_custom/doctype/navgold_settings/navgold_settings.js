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

			frm.add_custom_button(
				__('Import Listings'),
				() => {
					frappe.confirm(
						__(
							'This will download the full Amazon listings report and update all matching Proxy SKUs with ASIN, FBA status, shipping group, and active status. Continue?',
						),
						async () => {
							const response = await frappe.call({
								method:
									'navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku.bulk_import_listings',
								freeze: true,
								freeze_message: __('Queuing listings import...'),
							});
							if (response.message) {
								frappe.show_alert({
									message: response.message.message,
									indicator: 'blue',
								});
							}
						},
					);
				},
				__('Amazon Pricing'),
			);

			// Fetch FNSKU for all FBA Proxy SKUs with missing FNSKU
			frm.add_custom_button(
				__('Fetch FNSKU'),
				() => {
					frappe.confirm(
						__(
							'This will fetch FNSKU for all FBA Proxy SKUs with missing FNSKU. Continue?',
						),
						async () => {
							const response = await frappe.call({
								method:
									'navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku.bulk_fetch_fnsku',
								freeze: true,
								freeze_message: __('Queuing FNSKU fetch...'),
							});
							if (response.message) {
								frappe.show_alert({
									message: response.message.message,
									indicator: 'blue',
								});
							}
						},
					);
				},
				__('Amazon Pricing'),
			);
		}
	},

	async group_strategies_into_pricing(frm) {
		if (
			!frm.doc.strategy_name_mapping ||
			frm.doc.strategy_name_mapping.length === 0
		) {
			frappe.msgprint(__('Please configure Strategy Name Mapping first.'));
			return;
		}

		frappe.confirm(
			__(
				'This will group orphaned Amazon Product Strategy records into Amazon Product Pricing records using the configured mapping. Continue?',
			),
			async () => {
				await frappe.call({
					doc: frm.doc,
					method: 'group_strategies_into_pricing',
				});
			},
		);
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
