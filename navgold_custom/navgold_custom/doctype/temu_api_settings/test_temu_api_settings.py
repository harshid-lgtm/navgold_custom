import unittest

from navgold_custom.navgold_custom.doctype.temu_api_settings.temu_api_settings import (
	generate_temu_signature,
	serialize_sign_value,
)


class TestTemuAPISettings(unittest.TestCase):
	def test_signing_is_stable_and_excludes_sign(self):
		payload = {
			"app_key": "abc",
			"access_token": "token",
			"type": "bg.order.list.v2.get",
			"timestamp": 1711009072,
			"data_type": "JSON",
			"parentOrderSnList": ["PO-123"],
			"pageNumber": 1,
			"pageSize": 10,
		}
		first = generate_temu_signature(payload, "secret")
		payload["sign"] = "OLD"
		second = generate_temu_signature(payload, "secret")
		self.assertEqual(first, second)
		self.assertEqual(len(first), 32)
		self.assertEqual(first, first.upper())

	def test_nested_sign_values_use_compact_json(self):
		self.assertEqual(serialize_sign_value(["PO-1", "PO-2"]), '["PO-1","PO-2"]')
		self.assertEqual(serialize_sign_value(True), "true")


if __name__ == "__main__":
	unittest.main()
