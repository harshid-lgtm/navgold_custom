# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.data import cint, cstr, flt, now_datetime

from navgold_custom.navgold_custom.amazon_pricing.live_pricing_fetcher import fetch_live_prices

PRICING_FIELDS = [
	"name",
	"proxy_sku",
	"active_strategy",
	"is_active",
	"fixed_price",
	"fixed_price_value",
]


def resolve_system_status(pricing) -> str:
	if pricing is None:
		return "Not in System"
	return "Active" if cint(pricing.is_active) else "Disabled"


def build_snapshot_row(sku: str, live: dict, pricing) -> dict:
	selling_price = live.get("selling_price")
	return {
		"pricing": pricing.name if pricing else None,
		"proxy_sku": pricing.proxy_sku if pricing else sku,
		"active_strategy": pricing.active_strategy if pricing else None,
		"min_price": flt(live["min"]),
		"max_price": flt(live["max"]),
		"fixed_price": cint(pricing.fixed_price) if pricing else 0,
		"fixed_price_value": flt(pricing.fixed_price_value) if pricing else 0,
		"live_selling_price": flt(selling_price) if selling_price is not None else None,
		"source": "Amazon",
		"system_status": resolve_system_status(pricing),
	}


class AmazonPricingSnapshot(Document):
	def get_rows(self) -> list[dict]:
		return frappe.parse_json(self.snapshot_data or "[]")

	def populate_from_amazon_listings(self) -> int:
		# Amazon is the source of truth. If the live pull fails there is nothing to capture, so abort.
		live_prices = fetch_live_prices()

		pricing_map = {
			row.name: row for row in frappe.get_all("Amazon Product Pricing", fields=PRICING_FIELDS)
		}

		data = []
		for sku, live in live_prices.items():
			# No seller-allowed band on Amazon means no price to roll back to — skip those.
			if not (live.get("min") and live.get("max")):
				continue
			data.append(build_snapshot_row(sku, live, pricing_map.get(sku)))

		self.snapshot_datetime = now_datetime()
		self.item_count = len(data)
		self.snapshot_data = frappe.as_json(data)
		self.amazon_sourced_count = len(data)
		self.price_source = "Amazon Live"
		return len(data)

	@frappe.whitelist()
	def capture_now(self) -> dict:
		frappe.only_for("System Manager")
		# A captured snapshot is an immutable known-good point — never overwrite it.
		if self.get_rows():
			frappe.throw(_("This snapshot already holds captured prices. Create a new one instead."))
		if not self.trigger_type:
			self.trigger_type = "Manual"
			self.save()
		# The live pull paginates the whole catalogue — too slow to block the request.
		frappe.enqueue(
			"navgold_custom.navgold_custom.doctype.amazon_pricing_snapshot.amazon_pricing_snapshot.process_capture",
			queue="long",
			timeout=1800,
			snapshot_name=self.name,
			user=frappe.session.user,
		)
		return {"queued": True}

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

		# A preview selection can only shrink the set, never widen it.
		if pricing_names is not None:
			selected = set(
				frappe.parse_json(pricing_names) if isinstance(pricing_names, str) else pricing_names
			)
			rows = [row for row in rows if row.get("pricing") in selected]
			if not rows:
				frappe.throw(_("None of the selected SKUs are in this snapshot."))

		# Honour "Publish Selected Prices Only" so a snapshot publish never pushes wider than a sync.
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

		# Both publishers are whitelisted with a `pricing_names: str` annotation, so serialize
		# to JSON (their bodies parse_json it back) to satisfy the type validation.
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

	@frappe.whitelist()
	def publish_edited(self, updates: str | list, live: bool = False) -> dict:
		# Direct push of table-edited bands to Amazon. The captured snapshot is never mutated;
		# the edits live only in the request, so this snapshot stays an immutable rollback point.
		frappe.only_for("System Manager")

		from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
			get_allowed_pricing_proxy_skus,
		)
		from navgold_custom.navgold_custom.amazon_pricing.manual_price_push import push_manual_prices

		updates = frappe.parse_json(updates) if isinstance(updates, str) else updates

		# An edit can only shrink the snapshot — never push a SKU it never captured.
		snapshot_skus = {row.get("proxy_sku") for row in self.get_rows() if row.get("proxy_sku")}
		updates = [update for update in updates if cstr(update.get("sku")).strip() in snapshot_skus]
		if not updates:
			frappe.throw(_("None of the edited SKUs are in this snapshot."))

		# Honour "Publish Selected Prices Only" so a snapshot push never goes wider than a sync.
		allowed = get_allowed_pricing_proxy_skus()
		if allowed is not None:
			updates = [update for update in updates if cstr(update.get("sku")).strip() in allowed]
		if not updates:
			frappe.throw(_("No edited SKUs are in the current 'Publish Selected Prices Only' list."))

		return push_manual_prices(frappe.as_json(updates), live=live)


@frappe.whitelist()
def get_canary_proxy_skus() -> list:
	# The "Publish Selected Prices Only" list from Navgold Settings — the canary allowlist.
	# Empty means no restriction is configured (mode is "Publish All Prices").
	from navgold_custom.navgold_custom.amazon_pricing.amazon_feed_builder import (
		get_allowed_pricing_proxy_skus,
	)

	allowed = get_allowed_pricing_proxy_skus()
	return sorted(allowed) if allowed is not None else []


def process_capture(snapshot_name: str, user: str | None = None) -> None:
	target_user = user or frappe.session.user
	snapshot = frappe.get_doc("Amazon Pricing Snapshot", snapshot_name)
	try:
		count = snapshot.populate_from_amazon_listings()
	except Exception as exception:
		# The live pull is the only price source now, so a failure means there is nothing to
		# capture — tell the waiting form instead of letting it hang on the captured event.
		frappe.log_error(f"Live price fetch failed: {exception!s}", "Amazon Pricing Snapshot")
		frappe.publish_realtime(
			"amazon_snapshot_capture_failed",
			{"name": snapshot_name, "error": str(exception)},
			user=target_user,
		)
		raise
	snapshot.save()
	# Persist the captured snapshot before notifying the user it is ready.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	frappe.publish_realtime(
		"amazon_snapshot_captured",
		{"name": snapshot_name, "item_count": count, "price_source": snapshot.price_source},
		user=target_user,
	)


def capture_snapshot(trigger: str = "Manual", notes: str | None = None) -> str:
	snapshot = frappe.new_doc("Amazon Pricing Snapshot")
	snapshot.trigger_type = trigger
	snapshot.notes = notes
	snapshot.populate_from_amazon_listings()
	snapshot.insert(ignore_permissions=True)
	return snapshot.name


@frappe.whitelist()
def capture_snapshot_now(notes: str | None = None) -> dict:
	frappe.only_for("System Manager")
	name = capture_snapshot(trigger="Manual", notes=notes)
	return {"name": name}


def daily_pricing_snapshot():
	# Morning capture so there is always a same-day known-good price set to roll back to.
	capture_snapshot(trigger="Scheduled", notes="Automatic daily morning snapshot")
