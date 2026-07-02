// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

// A human editing the value pins it: value_mode flips to Manual so feeds
// never overwrite the hand-set value (sticky). Feeds stamp Auto server-side.
frappe.ui.form.on('Amazon Product Cost', 'value', (frm, cdt, cdn) => {
	const row = locals[cdt][cdn];
	if (row.value_mode !== 'Manual') {
		frappe.model.set_value(cdt, cdn, 'value_mode', 'Manual');
	}
});
