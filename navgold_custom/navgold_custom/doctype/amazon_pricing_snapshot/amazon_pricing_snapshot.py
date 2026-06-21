# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.data import cint, flt, now_datetime

SNAPSHOT_FIELDS = [
	"name",
	"proxy_sku",
	"active_strategy",
	"current_min_price",
	"current_max_price",
	"fixed_price",
	"fixed_price_value",
]


class AmazonPricingSnapshot(Document):
	def get_rows(self) -> list[dict]:
		return frappe.parse_json(self.snapshot_data or "[]")

	def populate_from_current_pricing(self) -> int:
		rows = frappe.get_all(
			"Amazon Product Pricing",
			filters={"is_active": 1},
			fields=SNAPSHOT_FIELDS,
		)
		data = [
			{
				"pricing": row.name,
				"proxy_sku": row.proxy_sku,
				"active_strategy": row.active_strategy,
				"min_price": flt(row.current_min_price),
				"max_price": flt(row.current_max_price),
				"fixed_price": cint(row.fixed_price),
				"fixed_price_value": flt(row.fixed_price_value),
			}
			for row in rows
		]
		self.snapshot_datetime = now_datetime()
		self.item_count = len(data)
		self.snapshot_data = frappe.as_json(data)
		return len(data)

	@frappe.whitelist()
	def capture_now(self) -> dict:
		frappe.only_for("System Manager")
		# Refuse to clobber a snapshot that already holds a captured price set — it is meant
		# to be an immutable known-good point to roll back to.
		if self.get_rows():
			frappe.throw(_("This snapshot already holds captured prices. Create a new one instead."))
		if not self.trigger_type:
			self.trigger_type = "Manual"
		count = self.populate_from_current_pricing()
		self.save()
		frappe.msgprint(_("Captured current prices for {0} SKU(s).").format(count))
		return {"item_count": count}

	@frappe.whitelist()
	def revert_prices(self) -> dict:
		frappe.only_for("System Manager")
		rows = self.get_rows()
		if not rows:
			frappe.throw(_("This snapshot has no data to revert to."))

		existing = set(
			frappe.get_all(
				"Amazon Product Pricing",
				filters={"name": ["in", [row["pricing"] for row in rows]]},
				pluck="name",
			)
		)

		updates = {}
		for row in rows:
			if row["pricing"] not in existing:
				continue
			updates[row["pricing"]] = {
				"active_strategy": row.get("active_strategy"),
				"current_min_price": flt(row.get("min_price")),
				"current_max_price": flt(row.get("max_price")),
				"fixed_price": cint(row.get("fixed_price")),
				"fixed_price_value": flt(row.get("fixed_price_value")),
			}

		if updates:
			frappe.db.bulk_update("Amazon Product Pricing", updates)

		self.db_set("status", "Reverted")
		frappe.msgprint(
			_("Reverted {0} pricing record(s) in ERP. Now click Publish to push them out.").format(
				len(updates)
			)
		)
		return {"reverted": len(updates), "skipped": len(rows) - len(updates)}

	@frappe.whitelist()
	def publish(self, channel: str, pricing_names: str | list | None = None) -> dict:
		frappe.only_for("System Manager")
		rows = [row for row in self.get_rows() if row.get("pricing")]
		if not rows:
			frappe.throw(_("This snapshot has no SKUs to publish."))

		# When the user ticks a subset in the preview, narrow to it. Selection can only ever
		# shrink the set, never widen it beyond what this snapshot already captured.
		if pricing_names is not None:
			selected = set(
				frappe.parse_json(pricing_names) if isinstance(pricing_names, str) else pricing_names
			)
			rows = [row for row in rows if row.get("pricing") in selected]
			if not rows:
				frappe.throw(_("None of the selected SKUs are in this snapshot."))

		# Honour "Publish Selected Prices Only" the same way the scheduled publishers do,
		# so a snapshot publish can never push a wider set than a normal sync would.
		from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
			get_allowed_pricing_proxy_skus,
		)

		allowed = get_allowed_pricing_proxy_skus()
		if allowed is not None:
			rows = [row for row in rows if row.get("proxy_sku") in allowed]
		if not rows:
			frappe.throw(
				_("No SKUs in this snapshot are in the current 'Publish Selected Prices Only' list.")
			)

		# Both publishers are @frappe.whitelist() with a `pricing_names: str` annotation, so the
		# framework's type validation rejects a raw list even on direct Python calls. Serialize to
		# JSON (their bodies parse_json it straight back) to match that documented contract.
		pricing_names = frappe.as_json([row["pricing"] for row in rows])

		if channel == "Repricer":
			from navgold_custom.navgold_custom.amazon_pricing.repricer_client import bulk_sync_to_repricer

			return bulk_sync_to_repricer(pricing_names)

		if channel == "Amazon":
			from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_submitter import (
				bulk_submit_pricing,
			)

			return bulk_submit_pricing(pricing_names)

		frappe.throw(_("Unknown publish channel {0}").format(channel))


def capture_snapshot(trigger: str = "Manual", notes: str | None = None) -> str:
	snapshot = frappe.new_doc("Amazon Pricing Snapshot")
	snapshot.trigger_type = trigger
	snapshot.notes = notes
	snapshot.populate_from_current_pricing()
	snapshot.insert(ignore_permissions=True)
	return snapshot.name


@frappe.whitelist()
def take_snapshot_now(notes: str | None = None) -> dict:
	frappe.only_for("System Manager")
	name = capture_snapshot(trigger="Manual", notes=notes)
	return {"name": name}


def daily_pricing_snapshot():
	# Morning capture so there is always a same-day known-good price set to roll back to.
	capture_snapshot(trigger="Scheduled", notes="Automatic daily morning snapshot")
