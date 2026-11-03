# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.utils import add_days, now_datetime

RETENTION_DAYS = 90
TERMINAL_STATUSES = ("Completed", "Error", "Dry Run", "Skipped")
HEAVY_FIELDS = ("feed_content", "response_content")


def purge_old_submission_payloads(retention_days: int = RETENTION_DAYS) -> int:
	"""Clear heavy payload bodies from old terminal pricing submission logs.

	The audit row itself is retained. Pending/Submitted records are never touched.
	Returns the number of rows whose payload fields were cleared.
	"""
	cutoff = add_days(now_datetime(), -retention_days)
	rows = frappe.get_all(
		"Amazon Pricing Submission Log",
		filters={
			"status": ["in", list(TERMINAL_STATUSES)],
			"creation": ["<", cutoff],
		},
		or_filters=[
			["feed_content", "!=", ""],
			["response_content", "!=", ""],
		],
		pluck="name",
	)

	if not rows:
		return 0

	for batch_start in range(0, len(rows), 500):
		batch = rows[batch_start : batch_start + 500]
		frappe.db.set_value(
			"Amazon Pricing Submission Log",
			{"name": ["in", batch]},
			{
				"feed_content": None,
				"response_content": None,
			},
			update_modified=False,
		)

	return len(rows)


def scheduled_purge_old_submission_payloads() -> None:
	purge_old_submission_payloads()
