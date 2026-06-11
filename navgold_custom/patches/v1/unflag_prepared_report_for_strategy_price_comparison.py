import frappe

REPORT_NAME = "Amazon Strategy Price Comparison"


def execute():
	if not frappe.db.exists("Report", REPORT_NAME):
		return

	# The site auto-promoted this report to prepared_report even though it runs in
	# ~0.1s; reset it and keep the automation from flipping it back.
	frappe.db.set_value(
		"Report",
		REPORT_NAME,
		{"prepared_report": 0, "disable_prepared_report_automation": 1},
		update_modified=False,
	)
	# delete_doc (not db.delete) so Prepared Report's on_trash removes the attached result files
	stale_prepared_reports = frappe.get_all(
		"Prepared Report", filters={"report_name": REPORT_NAME}, pluck="name"
	)
	for prepared_report_name in stale_prepared_reports:
		frappe.delete_doc(
			"Prepared Report", prepared_report_name, ignore_permissions=True, delete_permanently=True
		)
