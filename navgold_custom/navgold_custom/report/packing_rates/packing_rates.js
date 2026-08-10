// Copyright (c) 2026, Navgold and contributors
// For license information, please see license.txt

frappe.query_reports['Packing Rates'] = {
	filters: [
		{
			fieldname: 'required_days',
			label: __('Required Days'),
			fieldtype: 'Int',
			default: 0,
			description: __('0 is today, 1 is yesterday and today, 2 is the day before onwards'),
		},
	],
};
