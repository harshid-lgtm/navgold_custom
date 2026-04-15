// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Amazon Pricing Submission Log', {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.submission_type === 'Amazon') {
			if (frm.doc.status === 'Submitted') {
				frm.add_custom_button(__('Check Status'), () => {
					frm.call({
						method: 'check_status',
						doc: frm.doc,
						freeze: true,
						freeze_message: __('Checking feed status...'),
						callback: (r) => {
							if (r.message) {
								frappe.msgprint(
									__('Status: {0}', [r.message.status || 'Unknown']),
								);
								frm.reload_doc();
							}
						},
					});
				});
			}

			if (frm.doc.status === 'Error') {
				frm.add_custom_button(__('Retry Submission'), () => {
					frappe.confirm(
						__('Are you sure you want to retry this submission?'),
						() => {
							frm.call({
								method: 'retry_submission',
								doc: frm.doc,
								freeze: true,
								freeze_message: __('Retrying submission...'),
								callback: (r) => {
									if (r.message?.success) {
										frappe.msgprint(__('Submission retried successfully'));
										frm.reload_doc();
									}
								},
							});
						},
					);
				});
			}
		}
	},
});
