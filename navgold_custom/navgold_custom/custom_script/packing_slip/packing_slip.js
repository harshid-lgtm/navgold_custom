// Copyright (c) 2025
// Navgold - MIT License

let item_to_delivery_note_map = {};

frappe.ui.form.on('Packing Slip', {
	custom_tote(frm) {
		if (!frm.doc.custom_tote) {
			frm.fields_dict.custom_item.set_data([]);
			frm.set_value('custom_item', '');
			item_to_delivery_note_map = {};
			return;
		}

		frappe
			.call({
				method:
					'navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip.get_draft_delivery_note_items',
				args: {
					tote: frm.doc.custom_tote,
				},
			})
			.then(({ message: items }) => {
				if (!items || items.length === 0) {
					frappe.msgprint(__('No items found for tote {0}', [frm.doc.custom_tote]));
					frm.fields_dict.custom_item.set_data([]);
					item_to_delivery_note_map = {};
					return;
				}

				const all_items = [];
				item_to_delivery_note_map = {};

				for (const item of items) {
					const item_display = `${item.item_code} - ${item.item_name}`;
					all_items.push(item_display);
					item_to_delivery_note_map[item_display] = item.delivery_note;
				}

				frm.fields_dict.custom_item.set_data(all_items);
			});
	},

	custom_item(frm) {
		if (frm.doc.custom_item && item_to_delivery_note_map[frm.doc.custom_item]) {
			frm.set_value('delivery_note', item_to_delivery_note_map[frm.doc.custom_item]);
		}
	},
});
