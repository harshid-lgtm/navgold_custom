import frappe
from frappe.query_builder import DocType
from frappe.utils import add_days, now_datetime


def execute():
	# One-time sweep before the feed-status poller ships: feeds submitted over 30 days ago are no
	# longer retrievable from Amazon, so their logs must not stay "Submitted" forever.
	submission_log = DocType("Amazon Pricing Submission Log")
	(
		frappe.qb.update(submission_log)
		.set(submission_log.status, "Error")
		.set(submission_log.error_message, "Stale submission — feed result no longer retrievable")
		.where(
			(submission_log.status == "Submitted")
			& (submission_log.submission_type == "Amazon")
			& (submission_log.submitted_at < add_days(now_datetime(), -30))
		)
	).run()
