import frappe
from frappe import _
from frappe.integrations.utils import make_get_request
from frappe.utils import cint, get_datetime, now_datetime, time_diff_in_seconds

LICENSE_URL = "https://hq.bwh.tech/api/v2/method/purchase_flow"
CACHE_KEY = "navgold_license_status"
REFRESH_LOCK = "navgold_license_refreshing"
CACHE_TTL = 6 * 60 * 60
DEFAULT_INTERVAL = 120

ALLOWED_DOCTYPES = {
	"Navgold Settings",
	"Error Log",
	"Access Log",
	"Activity Log",
	"View Log",
	"Scheduled Job Log",
	"Scheduled Job Type",
	"Email Queue",
	"Notification Log",
	"Version",
	"Comment",
	"Communication",
	"DocShare",
}


def feature_disabled():
	return bool(frappe.conf.get("disable_navgold_license")) or frappe.flags.in_test


def check_license():
	if feature_disabled():
		return
	try:
		flags = parse_flags(make_get_request(LICENSE_URL))
	except Exception:
		frappe.log_error("License check failed", "Navgold License Gate")
		return

	previous = frappe.cache().get_value(CACHE_KEY) or {}
	flags["disable_since"] = (
		(previous.get("disable_since") or str(now_datetime())) if flags["disable"] else None
	)
	frappe.cache().set_value(CACHE_KEY, flags, expires_in_sec=CACHE_TTL)


def parse_flags(response):
	payload = frappe.parse_json(response) if isinstance(response, str) else response
	if not isinstance(payload, dict):
		payload = {}
	return {
		"disable": bool(payload.get("disable_operations")),
		"warn": bool(payload.get("warn")),
		"nag_count": cint(payload["nag_count"]) if "nag_count" in payload else 1,
		"warn_interval": cint(payload["warn_interval"]) if "warn_interval" in payload else DEFAULT_INTERVAL,
		"cooling_period": cint(payload.get("cooling_period")),
		"lockout_interval": cint(payload.get("lockout_interval")),
	}


def is_blocked(status):
	if not status.get("disable") or not status.get("disable_since"):
		return False

	lock_minutes = cint(status.get("cooling_period"))
	open_minutes = cint(status.get("lockout_interval"))
	if open_minutes <= 0:
		return True
	cycle_minutes = lock_minutes + open_minutes

	elapsed_minutes = time_diff_in_seconds(now_datetime(), get_datetime(status["disable_since"])) / 60
	return (elapsed_minutes % cycle_minutes) < lock_minutes


def get_status():
	status = frappe.cache().get_value(CACHE_KEY)
	if not status:
		request_refresh()
		return {"blocked": False, "warn": False, "nag_count": 0, "interval": DEFAULT_INTERVAL}

	return {
		"blocked": is_blocked(status),
		"warn": bool(status.get("warn")),
		"nag_count": cint(status.get("nag_count", 1)),
		"interval": cint(status.get("warn_interval")) or DEFAULT_INTERVAL,
	}


def request_refresh():
	if feature_disabled():
		return
	if frappe.cache().get_value(REFRESH_LOCK):
		return
	frappe.cache().set_value(REFRESH_LOCK, 1, expires_in_sec=120)
	frappe.enqueue(
		"navgold_custom.navgold_custom.license_gate.check_license",
		queue="short",
		enqueue_after_commit=True,
	)


def is_desk_user_action():
	if not getattr(frappe.local, "request", None):
		return False
	if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
		return False
	if frappe.flags.in_test or frappe.flags.in_import:
		return False
	if frappe.get_request_header("Authorization"):
		return False
	if frappe.form_dict.get("api_key") or frappe.form_dict.get("api_secret"):
		return False
	return frappe.session.user != "Guest"


def enforce(doc, method=None):
	if doc.doctype in ALLOWED_DOCTYPES:
		return
	if not is_desk_user_action():
		return
	if get_status()["blocked"]:
		frappe.throw(
			_("This system is locked pending payment. Please make the payment to resume operations."),
			title=_("Payment Required"),
		)


def client_state():
	status = get_status()
	raw = frappe.cache().get_value(CACHE_KEY) or {}
	return {
		"navgold_payment_warn": status["warn"],
		"navgold_payment_nag_count": status["nag_count"],
		"navgold_payment_warn_interval": status["interval"],
		"navgold_payment_disable": bool(raw.get("disable")),
		"navgold_payment_disable_since": raw.get("disable_since"),
		"navgold_payment_lock_sec": cint(raw.get("cooling_period")) * 60,
		"navgold_payment_open_sec": cint(raw.get("lockout_interval")) * 60,
		"navgold_payment_server_now": str(now_datetime()),
	}


def get_bootinfo(bootinfo):
	bootinfo.update(client_state())
	bootinfo.navgold_payment_dev_poll = bool(frappe.conf.get("navgold_license_dev_poll"))


@frappe.whitelist()
def refresh_now():
	check_license()
	return client_state()
