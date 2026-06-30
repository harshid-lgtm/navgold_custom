import frappe
from frappe import _

DAYS_OF_WEEK = [
	"Sunday",
	"Monday",
	"Tuesday",
	"Wednesday",
	"Thursday",
	"Friday",
	"Saturday",
]


def validate_lead_time(doc, method=None):
	if not doc.custom_lead_time:
		frappe.throw(_("Please add lead time for all days of the week."))

	seen_days = set()
	for row in doc.custom_lead_time:
		if row.day in seen_days:
			frappe.throw(_("Duplicate day found: {0}. Each day can only appear once.").format(row.day))
		seen_days.add(row.day)

	missing_days = set(DAYS_OF_WEEK) - seen_days
	if missing_days:
		frappe.throw(
			_("Missing days: {0}. All days of the week are required.").format(
				", ".join(sorted(missing_days, key=DAYS_OF_WEEK.index))
			)
		)


def propagate_sfp_eligibility(doc, method=None):
	if not doc.has_value_changed("custom_sfp_eligible") or not doc.custom_sfp_eligible:
		# Turning the flag off is intentionally non-destructive: the live
		# supplier-level gate in the feed builder already neutralizes per-row
		# SFP flags, so existing rows keep their manually-set values.
		return

	row_count = frappe.db.count("Supplier Item Price and Stock", {"supplier": doc.name})
	if not row_count:
		return

	if row_count > 100:
		frappe.enqueue(
			set_sfp_eligible_for_supplier_items,
			supplier=doc.name,
			now=frappe.flags.in_test,
		)
	else:
		set_sfp_eligible_for_supplier_items(doc.name)


def set_sfp_eligible_for_supplier_items(supplier):
	# ponytail: re-enabling a supplier re-marks all its rows sfp=1 by design (spec),
	# wiping prior per-item opt-outs; revisit if opt-outs must survive a disable/re-enable cycle.
	frappe.db.set_value(
		"Supplier Item Price and Stock",
		{"supplier": supplier},
		"sfp",
		1,
		update_modified=False,
	)
