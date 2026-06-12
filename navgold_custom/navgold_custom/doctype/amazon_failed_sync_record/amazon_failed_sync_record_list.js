// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.listview_settings['Amazon Failed Sync Record'] = {
	add_fields: ['status'],

	get_indicator: (doc) => {
		if (doc.status === 'Resolved') {
			return [__('Resolved'), 'green', 'status,=,Resolved'];
		}
		return [__('Failed'), 'red', 'status,=,Failed'];
	},

	onload: (listview) => {
		listview.page.add_action_item(__('Retry Selected'), async () => {
			const selected = listview.get_checked_items();
			if (selected.length === 0) {
				frappe.msgprint(__('Please select at least one record'));
				return;
			}

			const record_names = selected.map((doc) => doc.name);

			const confirmed = await navgold.confirm_dialog(
				__(
					'Retry syncing {0} failed record(s)? Retries run in the background.',
					[record_names.length],
				),
			);
			if (!confirmed) return;

			const response = await frappe.call({
				method:
					'navgold_custom.navgold_custom.doctype.amazon_failed_sync_record.amazon_failed_sync_record.bulk_retry_failed_syncs',
				args: {
					names: JSON.stringify(record_names),
				},
				freeze: true,
				freeze_message: __('Queueing retries...'),
			});

			frappe.show_alert(
				{
					message: __('Queued {0} record(s) for retry', [
						response.message.queued,
					]),
					indicator: 'green',
				},
				5,
			);
			listview.refresh();
		});
	},
};
