const DAYS_OF_WEEK = [
	'Sunday',
	'Monday',
	'Tuesday',
	'Wednesday',
	'Thursday',
	'Friday',
	'Saturday',
];

frappe.ui.form.on('Supplier', {
	refresh(frm) {
		if (!frm.doc.custom_lead_time?.length) {
			for (const day of DAYS_OF_WEEK) {
				frm.add_child('custom_lead_time', {
					day: day,
					lead_time_before_12: 1,
					lead_time_after_12: 1,
				});
			}
			frm.refresh_field('custom_lead_time');
		}
	},
});
