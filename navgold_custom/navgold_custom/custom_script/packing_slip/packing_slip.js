// Copyright (c) 2025
// Navgold - MIT License

frappe.ui.form.on('Packing Slip', {
	custom_scan_item(frm) {
		const barcode = frm.doc.custom_scan_item;

		if (!barcode) {
			return;
		}

		if (!frm.doc.custom_tote) {
			frappe.msgprint({
				title: __('Tote Required'),
				message: __('Please scan or enter Tote first before scanning items'),
				indicator: 'red',
			});
			return;
		}

		frappe.show_alert(
			{
				message: __('Scanning barcode...'),
				indicator: 'blue',
			},
			2,
		);

		frappe.call({
			method:
				'navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip.scan_barcode_and_get_delivery_note',
			args: {
				search_value: barcode,
				tote: frm.doc.custom_tote,
			},
			callback: (r) => {
				if (r.message?.error) {
					frappe.show_alert(
						{
							message: r.message.error,
							indicator: 'red',
						},
						5,
					);
					return;
				}

				if (r.message?.delivery_note) {
					frappe.show_alert(
						{
							message: __('Item {0} found in Delivery Note {1}', [
								r.message.item_code,
								r.message.delivery_note,
							]),
							indicator: 'green',
						},
						3,
					);

					frm.set_value('delivery_note', r.message.delivery_note);

					const item_display = `${r.message.item_code} - ${r.message.item_name}`;
					frm.set_value('custom_item', item_display);
				}
			},
			error: () => {
				frappe.show_alert(
					{
						message: __('Error scanning barcode'),
						indicator: 'red',
					},
					5,
				);
			},
		});
	},
});
