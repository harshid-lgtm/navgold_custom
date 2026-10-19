import frappe
from frappe import _
from frappe.integrations.utils import make_get_request
from frappe.utils import cint, get_datetime, now_datetime, time_diff_in_hours

SETTINGS = "Navgold Settings"

ALLOWED_DOCTYPES = {
	SETTINGS,
	"Error Log",
	"Access Log",
	"Activity Log",
	"View Log",
	"Scheduled Job Log",
	"Scheduled Job Type",
	"Email Queue",
	"Notification Log",
}


def check_license():
	settings = frappe.get_cached_doc(SETTINGS)
	url = (settings.license_check_url or "").strip()
	if not url:
		return

	now = now_datetime()
	try:
		response = make_get_request(url)
		active = parse_active(response)
	except Exception:
		frappe.db.set_single_value(SETTINGS, "last_license_check", now, update_modified=False)
		frappe.log_error("License check failed", "Navgold License Gate")
		return

	values = {"license_active": cint(active), "last_license_check": now}
	if active:
		values["last_license_ok"] = now
	frappe.db.set_single_value(SETTINGS, values, update_modified=False)


def parse_active(response):
	payload = frappe.parse_json(response) if isinstance(response, str) else response
	if isinstance(payload, dict):
		for key in ("active", "checked", "paid", "enabled", "enable"):
			if key in payload:
				return bool(payload[key])
		return False
	return bool(payload)


def is_locked():
	settings = frappe.get_cached_doc(SETTINGS)
	if not (settings.license_check_url or "").strip():
		return False

	if not settings.last_license_ok:
		return not cint(settings.license_active)

	if cint(settings.license_active):
		grace = cint(settings.license_grace_hours) or 48
		stale_hours = time_diff_in_hours(now_datetime(), get_datetime(settings.last_license_ok))
		return stale_hours > grace

	return True


def enforce(doc, method=None):
	if not getattr(frappe.local, "request", None):
		return
	if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch or frappe.flags.in_test:
		return
	if doc.doctype in ALLOWED_DOCTYPES:
		return
	if is_locked():
		frappe.throw(
			_("This system is locked pending payment. Please make the payment to resume operations."),
			title=_("Payment Required"),
		)


def get_bootinfo(bootinfo):
	bootinfo.navgold_payment_locked = is_locked()
