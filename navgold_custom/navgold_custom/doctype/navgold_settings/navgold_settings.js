// Copyright (c) 2025, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Navgold Settings', {
	refresh(frm) {
		if (frm.doc.pricing_sync_mode === 'Live') {
			frm.add_custom_button(
				__('Revert to Dry Run'),
				async () => {
					await frappe.call({
						doc: frm.doc,
						method: 'revert_to_dry_run',
					});
					frm.reload_doc();
				},
				__('Go Live'),
			);
		} else {
			frm.add_custom_button(
				__('Approve & Go Live'),
				async () => {
					const confirmed = await navgold.confirm_dialog(
						__(
							'This switches Pricing Sync to LIVE. Real prices and feeds will be pushed to Amazon and Repricer. Continue?',
						),
					);
					if (!confirmed) return;

					await frappe.call({
						doc: frm.doc,
						method: 'approve_and_go_live',
					});
					frm.reload_doc();
				},
				__('Go Live'),
			);
		}

		if (frm.doc.amazon_pricing_enabled) {
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
				__('Dry Run (No Push)'),
				() => {
					frappe.prompt(
						{
							fieldname: 'refresh_first',
							fieldtype: 'Check',
							label: __('Refresh strategies first'),
							default: 0,
							description: __(
								'Recalculates and writes prices to ERP before rendering. Leave off to render the prices exactly as they stand now.',
							),
						},
						async (values) => {
							const response = await frappe.call({
								method:
									'navgold_custom.navgold_custom.amazon_pricing.pricing_dry_run.dry_run_pricing',
								args: { refresh_first: values.refresh_first },
								freeze: true,
								freeze_message: __('Rendering feed (no push)...'),
							});
							const result = response.message;
							frappe.msgprint({
								title: __('Dry Run Complete — Nothing Pushed'),
								indicator: result.valid ? 'green' : 'red',
								message: __(
									'Rendered {0} message(s). {1} mover(s), {2} new, {3} dropped. {4} deviation-blocked, {5} validation-blocked.<br><br>Open snapshot <a href="/app/amazon-pricing-snapshot/{6}">{6}</a> for the variance and rendered feed.',
									[
										result.message_count,
										result.variance.movers,
										result.variance.new,
										result.variance.dropped,
										result.blocked_price_deviation,
										result.blocked_validation,
										result.snapshot,
									],
								),
							});
						},
						__('Dry Run Pricing'),
						__('Run'),
					);
				},
				__('Amazon Pricing'),
			);

			frm.add_custom_button(
				__('Refresh All Costs'),
				async () => {
					const confirmed = await navgold.confirm_dialog(
						__(
							'This will re-pull synced cost values (PO-Sync, Supplier-Sync, FBA-Transfer, Auto-Sync) into every active strategy and recalculate min/max prices. Continue?',
						),
					);
					if (!confirmed) return;

					const response = await frappe.call({
						method:
							'navgold_custom.navgold_custom.doctype.amazon_product_strategy.amazon_product_strategy.refresh_all_strategy_costs',
						freeze: true,
						freeze_message: __('Queuing cost refresh...'),
					});
					frappe.show_alert({
						message: __('Queued {0} strategies across {1} chunks', [
							response.message.total,
							response.message.chunks_enqueued,
						]),
						indicator: 'blue',
					});
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
				__('Fetch Selling Prices'),
				async () => {
					const confirmed = await navgold.confirm_dialog(
						__(
							'This will fetch the live Amazon selling price for every active Proxy SKU and store it for the price-deviation guard. Continue?',
						),
					);
					if (!confirmed) return;

					const response = await frappe.call({
						method:
							'navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku.bulk_fetch_selling_prices',
						freeze: true,
						freeze_message: __('Queuing selling price fetch...'),
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
