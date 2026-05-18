// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Navgold Settings', {
	refresh(frm) {
		if (frm.doc.amazon_pricing_enabled) {
			if (frm.doc.repricer_enabled) {
				frm.add_custom_button(
					__('Sync Repricer Rules'),
					async () => {
						const response = await frappe.call({
							method:
								'navgold_custom.navgold_custom.amazon_pricing.repricer_client.sync_repricer_rules',
							freeze: true,
							freeze_message: __('Syncing rules from Repricer.com...'),
						});
						frappe.msgprint(
							__('Synced {0} rules, created {1} new rules', [
								response.message.synced,
								response.message.created,
							]),
						);
					},
					__('Amazon Pricing'),
				);
			}

			if (frm.doc.auto_sync_cogs && frm.doc.cogs_supplier) {
				frm.add_custom_button(
					__('Bulk Sync COGS'),
					async () => {
						const confirmed = await navgold.confirm_dialog(
							__(
								"This will sync COGS from supplier '{0}' to all active pricing records. Continue?",
								[frm.doc.cogs_supplier],
							),
						);
						if (!confirmed) return;

						const response = await frappe.call({
							method:
								'navgold_custom.navgold_custom.amazon_pricing.cogs_sync.bulk_sync_cogs',
							args: {
								supplier: frm.doc.cogs_supplier,
							},
							freeze: true,
							freeze_message: __('Syncing COGS...'),
						});
						frappe.msgprint(
							__('Synced {0} records, {1} errors', [
								response.message.synced,
								response.message.errors,
							]),
						);
					},
					__('Amazon Pricing'),
				);
			}

			frm.add_custom_button(
				__('Refresh All Strategies'),
				async () => {
					const response = await frappe.call({
						method:
							'navgold_custom.navgold_custom.amazon_pricing.strategy_selector.bulk_refresh_strategies',
						freeze: true,
						freeze_message: __('Refreshing strategies...'),
					});
					frappe.msgprint(
						__('Refreshed {0} records, {1} updated', [
							response.message.total,
							response.message.updated,
						]),
					);
				},
				__('Amazon Pricing'),
			);

			frm.add_custom_button(
				__('Import Listings'),
				async () => {
					const confirmed = await navgold.confirm_dialog(
						__(
							'This will download the full Amazon listings report and update all matching Proxy SKUs with ASIN, FBA status, shipping group, and active status. Continue?',
						),
					);
					if (!confirmed) return;

					const response = await frappe.call({
						method:
							'navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku.bulk_import_listings',
						freeze: true,
						freeze_message: __('Queuing listings import...'),
					});
					frappe.show_alert({
						message: response.message.message,
						indicator: 'blue',
					});
				},
				__('Amazon Pricing'),
			);

			frm.add_custom_button(
				__('Fetch FNSKU'),
				async () => {
					const confirmed = await navgold.confirm_dialog(
						__(
							'This will fetch FNSKU for all FBA Proxy SKUs with missing FNSKU. Continue?',
						),
					);
					if (!confirmed) return;

					const response = await frappe.call({
						method:
							'navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku.bulk_fetch_fnsku',
						freeze: true,
						freeze_message: __('Queuing FNSKU fetch...'),
					});
					frappe.show_alert({
						message: response.message.message,
						indicator: 'blue',
					});
				},
				__('Amazon Pricing'),
			);

			frm.add_custom_button(
				__('Fetch FBA Fees'),
				async () => {
					const confirmed = await navgold.confirm_dialog(
						__(
							'This will fetch the latest FBA fees from Amazon SP-API and update the FBA Fee cost on every active FBA strategy. Continue?',
						),
					);
					if (!confirmed) return;

					const response = await frappe.call({
						method:
							'navgold_custom.navgold_custom.amazon_pricing.fba_fee_fetcher.bulk_fetch_fba_fees',
						freeze: true,
						freeze_message: __('Queuing FBA Fee fetch...'),
					});
					frappe.show_alert({
						message: response.message.message,
						indicator: 'blue',
					});
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

		const confirmed = await navgold.confirm_dialog(
			__(
				'This will group orphaned Amazon Product Strategy records into Amazon Product Pricing records using the configured mapping. Continue?',
			),
		);
		if (!confirmed) return;

		await frappe.call({
			doc: frm.doc,
			method: 'group_strategies_into_pricing',
		});
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
