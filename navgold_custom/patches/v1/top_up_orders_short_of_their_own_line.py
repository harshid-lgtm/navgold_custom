# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from navgold_custom.navgold_custom.custom_script.sales_order.sales_order import (
	request_stock_for_uncovered_orders,
)


def execute():
	"""Top up the orders whose request no longer covers their own line.

	An order's request is sized once, against the shelf as it stood at submit, and nothing revisits
	it. When a unit it was counting on later leaves — picked for another order, written off, or
	corrected away by a stock count — the order is left short with no alert. SAL-ORD-2026-29870
	wanted 2, was sized while the shelf held 1, asked for 1, and then lost that unit to another
	order's delivery note.

	Runs the survivors through the same create_material_request the submit hook uses, so the
	quantities come from the audited path rather than a second implementation of the same decision.
	"""
	requested = request_stock_for_uncovered_orders()
	if requested:
		print(f"Raised {len(requested)} Material Requests for orders short of their own line.")
