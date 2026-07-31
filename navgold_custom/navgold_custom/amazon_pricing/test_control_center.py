# Copyright (c) 2026, developers@buildwithhussain.com and Contributors
# See license.txt

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.data import cint

from navgold_custom.navgold_custom.amazon_pricing import control_center


def get_live_freeze_value() -> int:
	return cint(frappe.db.get_single_value("Navgold Settings", "freeze_amazon_outbound"))


class TestContainNow(IntegrationTestCase):
	def run_contain_now(self, publish_side_effect):
		snapshot_doc = MagicMock()
		snapshot_doc.revert_prices.return_value = {"reverted": 1, "skipped": 0}
		snapshot_doc.publish.side_effect = publish_side_effect
		with (
			patch.object(control_center, "get_last_good_snapshot", return_value={"name": "SNAP-TEST"}),
			patch.object(control_center.frappe, "get_doc", return_value=snapshot_doc),
		):
			return control_center.contain_now(), snapshot_doc

	def test_publishes_through_an_active_freeze_and_ends_frozen(self):
		# The most likely incident state: the circuit breaker already tripped and froze
		# outbound — the corrective publish must still reach both channels.
		frappe.db.set_single_value("Navgold Settings", "freeze_amazon_outbound", 1)
		freeze_seen_by_publish = []

		def record_freeze(channel):
			freeze_seen_by_publish.append(get_live_freeze_value())
			return {"submitted": 1}

		result, snapshot_doc = self.run_contain_now(record_freeze)

		self.assertEqual(freeze_seen_by_publish, [0, 0])
		self.assertEqual(get_live_freeze_value(), 1)
		self.assertEqual(result["frozen"], 1)
		self.assertEqual(
			[call.args[0] for call in snapshot_doc.publish.call_args_list], ["Repricer", "Amazon"]
		)
		snapshot_doc.revert_prices.assert_called_once()

	def test_refreezes_even_when_a_publish_raises(self):
		frappe.db.set_single_value("Navgold Settings", "freeze_amazon_outbound", 0)

		with self.assertRaises(frappe.ValidationError):
			self.run_contain_now(frappe.ValidationError("Repricer unreachable"))

		self.assertEqual(get_live_freeze_value(), 1)
