import frappe

DAYS_OF_WEEK = [
	"Sunday",
	"Monday",
	"Tuesday",
	"Wednesday",
	"Thursday",
	"Friday",
	"Saturday",
]


def validate_lead_time(doc, method=None):
	if not doc.custom_lead_time:
		frappe.throw("Please add lead time for all days of the week.")

	seen_days = set()
	for row in doc.custom_lead_time:
		if row.day in seen_days:
			frappe.throw(f"Duplicate day found: {row.day}. Each day can only appear once.")
		seen_days.add(row.day)

	missing_days = set(DAYS_OF_WEEK) - seen_days
	if missing_days:
		frappe.throw(
			f"Missing days: {', '.join(sorted(missing_days, key=DAYS_OF_WEEK.index))}. All days of the week are required."
		)
