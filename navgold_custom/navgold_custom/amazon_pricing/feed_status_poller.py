# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, now_datetime
from frappe.utils.data import cint

from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_submitter import check_feed_status

POLL_BATCH_SIZE = 25
# Anything still "Submitted" after a week is effectively dead; the stale-log patch sweeps the
# older backlog to Error so it never re-enters this window.
FEED_POLL_WINDOW_DAYS = 7


def poll_pending_feed_submissions():
	settings = frappe.get_single("Amazon SP API Settings")
	if not cint(settings.is_active):
		return

	pending_log_names = frappe.get_all(
		"Amazon Pricing Submission Log",
		filters={
			"status": "Submitted",
			"submission_type": "Amazon",
			"feed_id": ("is", "set"),
			"submitted_at": (">=", add_days(now_datetime(), -FEED_POLL_WINDOW_DAYS)),
		},
		pluck="name",
		order_by="submitted_at asc",
		limit_page_length=POLL_BATCH_SIZE,
	)

	for log_name in pending_log_names:
		try:
			check_feed_status(log_name)
		except Exception:
			# One feed's failure must not block the rest of the batch.
			frappe.log_error(
				f"Feed status poll failed for {log_name}:\n{frappe.get_traceback()}",
				"Amazon Feed Status Poller",
			)
