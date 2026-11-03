# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from navgold_custom.navgold_custom.amazon_pricing.submission_log_retention import (
	purge_old_submission_payloads,
)


def execute():
	purge_old_submission_payloads()
