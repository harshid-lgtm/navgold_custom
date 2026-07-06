// Copyright (c) 2026, developers@buildwithhussain.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Cost Type', {
	source(frm) {
		// A fed leaf (source set) carries no default; clearing it here keeps
		// validate() from throwing when both source and default_value are present.
		// Visibility is handled declaratively by depends_on in cost_type.json.
		if (!frm.doc.is_group && frm.doc.source) {
			frm.set_value('default_value', 0);
		}
	},
});
