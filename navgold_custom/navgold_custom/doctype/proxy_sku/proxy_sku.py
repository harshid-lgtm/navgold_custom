# Copyright (c) 2025, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ProxySKU(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		asin: DF.Link | None
		is_active: DF.Check
		item: DF.Link | None
		naming_series: DF.Data | None
		uom: DF.Link | None
	# end: auto-generated types

	def autoname(self):
		# Allow manually set name to persist
		if getattr(self, "_user_provided_name", None):
			self.name = self._user_provided_name
			return

		# fallback to naming series
		if not self.naming_series:
			self.naming_series = f"{self.item}-{self.uom}-.####"

		self.name = frappe.model.naming.make_autoname(self.naming_series)

	def before_insert(self):
		# Preserve user-specified name before frappe resets it
		if self.name and self.name != f"New {self.doctype}":
			self._user_provided_name = self.name
