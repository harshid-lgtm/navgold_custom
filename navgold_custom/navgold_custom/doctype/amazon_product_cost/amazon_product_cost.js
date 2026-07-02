// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

// Value is editable only under Manual (the field's read_only_depends_on gates it). Switching a
// row back to Auto hands the value to the feed/default, so clear the stale hand-set number; the
// server re-derives it on save.
frappe.ui.form.on('Amazon Product Cost', 'value_mode', (frm, cdt, cdn) => {
	const row = locals[cdt][cdn];
	if (row.value_mode === 'Auto' && row.value) {
		frappe.model.set_value(cdt, cdn, 'value', 0);
	}
});
