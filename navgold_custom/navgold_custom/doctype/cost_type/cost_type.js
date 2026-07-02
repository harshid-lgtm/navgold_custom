// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Cost Type', {
	refresh(frm) {
		toggle_default_value(frm);
	},

	source(frm) {
		// A fed leaf (source set) carries no default; clearing it here keeps
		// validate() from throwing when both source and default_value are present.
		if (!frm.doc.is_group && frm.doc.source) {
			frm.set_value('default_value', 0);
		}
		toggle_default_value(frm);
	},
});

function toggle_default_value(frm) {
	frm.toggle_display('default_value', !frm.doc.is_group && !frm.doc.source);
}
