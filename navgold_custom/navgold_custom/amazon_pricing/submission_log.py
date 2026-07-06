# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe

from navgold_custom.navgold_custom.amazon_pricing.alerts import alert_feed_error


def record_submission_error(
	log,
	message: str | None = None,
	channel: str = "Amazon",
	exception: Exception | None = None,
	alert: bool = True,
) -> None:
	if exception is not None and message is None:
		traceback = frappe.get_traceback(with_context=True)
		message = f"{exception!s}\n\n{traceback}" if traceback else str(exception)

	log.status = "Error"
	log.error_message = message

	error_log = frappe.log_error(title=f"{channel} pricing submission failed: {log.name}", message=message)
	if error_log:
		log.error_log = error_log.name

	log.save()

	if alert:
		alert_feed_error(log.name, message, channel)

	if not frappe.flags.in_test:
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
