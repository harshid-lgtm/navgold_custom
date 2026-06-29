# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.data import cstr, flt

from navgold_custom.navgold_custom.amazon_pricing.manual_price_push import (
	load_updates_from_attachment,
	push_manual_prices,
)

PRICING_FIELDS = [
	"name",
	"proxy_sku",
	"current_min_price",
	"current_max_price",
	"fixed_price",
	"fixed_price_value",
]


class AmazonBulkPricePush(Document):
	def get_updates(self) -> list[dict]:
		# Rename grid columns to the wire keys; push_manual_prices owns the strip/flt/filter.
		return [
			{"sku": row.sku, "min": row.min_price, "max": row.max_price, "our_price": row.our_price}
			for row in self.rows
		]

	@frappe.whitelist()
	def load_from_file(self) -> dict:
		frappe.only_for("System Manager")
		if not self.upload_file:
			frappe.throw(_("Attach a CSV or XLSX file first."))

		updates = load_updates_from_attachment(self.upload_file)
		if not updates:
			frappe.throw(_("No rows found in the uploaded file."))

		self.set("rows", [])
		for update in updates:
			self.append(
				"rows",
				{
					"sku": update["sku"],
					"min_price": update["min"],
					"max_price": update["max"],
					"our_price": update["our_price"],
					"price_source": "File",
				},
			)
		self.status = "Draft"
		self.save()
		return {"loaded": len(updates)}

	def add_pricing_rows(self, pricing_rows: list[dict]) -> int:
		existing = {cstr(row.sku).strip() for row in self.rows}
		added = 0
		for pricing in pricing_rows:
			if pricing["sku"] in existing:
				continue
			self.append("rows", pricing)
			existing.add(pricing["sku"])
			added += 1
		self.save()
		return added

	@frappe.whitelist()
	def add_rows(self, proxy_skus: str | list | None = None, filters: str | dict | None = None) -> dict:
		frappe.only_for("System Manager")
		pricing_rows = fetch_pricing_for_bulk(proxy_skus=proxy_skus, filters=filters)
		return {"added": self.add_pricing_rows(pricing_rows)}

	@frappe.whitelist()
	def dry_run(self) -> dict:
		frappe.only_for("System Manager")
		result = push_manual_prices(self.get_updates(), live=False)
		self.db_set("status", "Validated")
		return result

	@frappe.whitelist()
	def push_live(self) -> dict:
		frappe.only_for("System Manager")
		updates = self.get_updates()

		# Validate before the optional snapshot so we never capture a rollback point
		# for a push that the guards would have rejected anyway.
		push_manual_prices(updates, live=False)

		if self.capture_snapshot_before_push:
			from navgold_custom.navgold_custom.doctype.amazon_pricing_snapshot.amazon_pricing_snapshot import (
				capture_snapshot_now,
			)

			snapshot = capture_snapshot_now(notes=f"Before bulk price push {self.name}")
			self.db_set("snapshot_taken", snapshot["name"])

		try:
			result = push_manual_prices(updates, live=True)
		except Exception:
			self.db_set("status", "Failed")
			raise

		self.db_set(
			{
				"status": "Pushed",
				"submission_log": result["log_name"],
				"push_result": _("Feed {0} (log {1}) — {2} SKU(s) submitted.").format(
					result["feed_id"], result["log_name"], result["sku_count"]
				),
			}
		)
		return result


@frappe.whitelist()
def fetch_pricing_for_bulk(
	proxy_skus: str | list | None = None, filters: str | dict | None = None
) -> list[dict]:
	# Shared by "Add SKUs" (explicit selection) and "Add by Filter" (e.g. all active FBA).
	# The Amazon Product Pricing name equals its Proxy SKU, which is the seller SKU the
	# feed carries — mirror the per-SKU dialog's `sku = proxy_sku`.
	frappe.only_for("System Manager")

	query_filters = {}
	if proxy_skus is not None:
		names = frappe.parse_json(proxy_skus) if isinstance(proxy_skus, str) else proxy_skus
		query_filters["name"] = ["in", names]
	elif filters:
		query_filters = frappe.parse_json(filters) if isinstance(filters, str) else filters
	else:
		frappe.throw(_("Pass either proxy_skus or filters."))

	rows = frappe.get_all("Amazon Product Pricing", filters=query_filters, fields=PRICING_FIELDS)
	return [
		{
			"proxy_sku": row.proxy_sku,
			"sku": row.name,
			"min_price": flt(row.current_min_price),
			"max_price": flt(row.current_max_price),
			"our_price": flt(row.fixed_price_value) if row.fixed_price else flt(row.current_min_price),
			"price_source": "Fixed Price" if row.fixed_price else "Current Min/Max",
		}
		for row in rows
	]
