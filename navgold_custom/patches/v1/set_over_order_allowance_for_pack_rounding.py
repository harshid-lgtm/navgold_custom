# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

from navgold_custom.setup import (
	OVER_ORDER_ALLOWANCE_PERCENT,
	set_over_order_allowance_for_pack_rounding,
)


def execute():
	previous = set_over_order_allowance_for_pack_rounding()
	if previous is None:
		return

	print(
		f"Raised Buying Settings over_order_allowance from {previous} "
		f"to {OVER_ORDER_ALLOWANCE_PERCENT} so whole-pack rounding stops failing."
	)
