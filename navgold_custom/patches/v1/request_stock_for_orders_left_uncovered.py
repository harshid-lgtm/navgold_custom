# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from navgold_custom.navgold_custom.custom_script.sales_order.sales_order import (
	request_stock_for_uncovered_orders,
)


def execute():
	requested = request_stock_for_uncovered_orders()
	if requested:
		print(
			f"Raised {len(requested)} Material Requests for orders left uncovered by supply raised for a different order."
		)
