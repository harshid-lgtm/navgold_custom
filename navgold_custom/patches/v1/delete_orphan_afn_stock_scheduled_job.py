import frappe


def execute():
	# An AFN stock cron was hand-created on prod (random hash name, e.g. "k9bqs53fl5") before the
	# hooks scheduler entry existed. sync_jobs runs after patches on migrate and recreates the job
	# from hooks, so drop only the orphan: hooks-generated jobs get deterministic names derived
	# from the method path, which the hand-created one lacks.
	job_names = frappe.get_all(
		"Scheduled Job Type",
		filters={"method": ["like", "%schedule_bulk_fetch_afn_stock"]},
		pluck="name",
	)
	for job_name in job_names:
		if job_name.endswith("schedule_bulk_fetch_afn_stock"):
			continue
		frappe.delete_doc("Scheduled Job Type", job_name, ignore_missing=True, force=True)
